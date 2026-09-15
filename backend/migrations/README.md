# 数据库迁移（手工 SQL + 自动体检）

## 这个目录是干什么的

本项目建表靠 `app/models/__init__.py` 里的 `init_db()`（内部是 SQLAlchemy 的
`Base.metadata.create_all`）。而 **`create_all` 只会建"不存在的表"，不会改"已存在的表"**。

所以同一个 schema 有两种产生方式，它们是两条独立的路：

| 场景 | 怎么做 | 要不要跑这里的迁移 |
|---|---|---|
| **A. 从零建库**（新机器、新环境） | 启动后端，`init_db()` 自动建表 | **不用**。模型里已经包含全部历史改动（T54/T72/T73/T78 的索引、加列、默认值都补进了模型；T81 删掉的 `chunks` 也已从模型移除），`create_all` 出来的就是最终结构 |
| **B. 升级已有库**（你手上这个库） | 按顺序执行本目录的 `.sql` | **要**。因为老表结构是"当时手工改的"，`create_all` 不会去动它 |

一句话记：**迁移文件是给"已经跑着的库"打的补丁，不是建库脚本。**

## 规范

文件名：`NNNN_tXX_描述.sql`（序号 = 执行顺序，`tXX` = 对应的任务编号）。

每个文件头部必须有这几段，缺一不可：

```
-- 目的：     为什么加这个（业务原因，不是"加了一列"这种废话）
-- 影响表：   改哪张表、加什么对象
-- 适用前提： 什么状态下才能执行（比如"表必须为 0 行"）
-- 幂等性：   能不能重复执行（绝大多数：不能）
-- 执行：     照抄的命令
-- 回滚：     反着做一遍的 SQL
```

**同时列出配套的代码改动**——本项目的迁移和代码必须同一批发布，只改一边就是中间态
（T73 就出现过"DB 已是 uuid、写入端还传字符串"的窗口期）。

## 执行

```bash
cd backend
./.venv/Scripts/python.exe migrations/apply.py --list                        # 看哪些跑过了
./.venv/Scripts/python.exe migrations/apply.py 0003_t73_citations_fk.sql     # 跑一条
```

**不要手敲 `docker exec`**。手动执行最容易漏 `-v ON_ERROR_STOP=1`：漏掉之后 psql
遇到报错会**继续往下跑、最后返回 0**——你会以为迁移成功了，实际只跑了一半。
`apply.py` 已经把这个参数和"执行记录"一起包好了。

## 验收：改完 schema 必须做这一步

```bash
cd backend
./.venv/Scripts/python.exe playground/schema_drift.py    # 退出码 0 = 无漂移
```

它会把「模型声明的结构」和「现库里真实的结构」逐列对账（类型 / nullable / 默认值 /
索引 / 外键），**外加表级差集** —— 库里多出来的表、模型有而库里没有的表，都算漂移。
（后半句是 T81 补的洞：这两类原先只 `print` 不计入 `issues`，于是增表 / 删表这类改动
**退出码恒为 0**，门禁形同虚设。见踩坑 #80。）
**改完模型 → 写一条迁移 SQL → 体检 0 漂移**，三步齐了才算改完。
退出码非 0 说明「从零建库」和「你正在用的库」会产生不同结构。

## 补录一条历史迁移（当时只在库里执行、没落盘的）

不要凭记忆写 SQL。正确做法是**按现库结构反推，再用临时 schema 重放验证等价**：

```sql
BEGIN;
CREATE SCHEMA tmp_verify;
-- ① 建最小表结构（只需要让迁移 SQL 有东西可改）
CREATE TABLE tmp_verify.tasks (id uuid PRIMARY KEY, project_id uuid NOT NULL, status varchar);
SET search_path = tmp_verify, public;
-- ② 把你写好的迁移 SQL 原样粘进来
CREATE UNIQUE INDEX uq_tasks_active_per_project ON tasks (project_id) WHERE status IN ('pending','running');
-- ③ 把"重放出来的定义"和"现库的定义"并排比，要求完全一致
SELECT
  replace((SELECT indexdef FROM pg_indexes WHERE schemaname='tmp_verify' AND indexname='uq_tasks_active_per_project'), 'tmp_verify.', 'public.')
  = (SELECT indexdef FROM pg_indexes WHERE schemaname='public' AND indexname='uq_tasks_active_per_project') AS 一致;
ROLLBACK;
```

两个坑（都实测踩过）：

1. **`pg_get_constraintdef` 会带 schema 限定符（`public.documents`）**，而临时 schema 里
   渲染出来是不带限定符的。这不代表语义不同——比较前先把限定符 `replace` 掉，否则会得到
   假的"不一致"。
2. **最小表结构要跟现库的类型对得上**。比如 `citations.kb_id` 本来就是 uuid（T73 只把
   `document_id` 从 varchar 改成 uuid），你要是照着"感觉"写成 varchar，重放会直接报
   `Key columns "kb_id" and "id" are of incompatible types`。

## 迁移清单

| 文件 | 内容 | 状态 |
|---|---|---|
| `0001_t54_tasks_active_unique.sql` | `tasks` 部分唯一索引 `uq_tasks_active_per_project`（同项目只允许一个活跃任务） | 已生效（补录） |
| `0002_t72_documents_vector_epoch.sql` | `documents.vector_epoch`（块代次，配合 Milvus 检索端的代次过滤） | 已生效（补录） |
| `0003_t73_citations_fk.sql` | `citations.document_id` varchar→uuid + 两个 `ON DELETE SET NULL` 外键 + 三个索引 | 已生效 |
| `0004_t78_vector_epoch_default_zero.sql` | `documents.vector_epoch` 默认值 1→0（修正「首次解析产出代次=2」） | 已生效 |
| `0005_t81_drop_chunks.sql` | `DROP TABLE chunks`（M1 遗留死表：0 行、零引用；块数据实际在 Milvus） | 已生效 |

执行明细见 `APPLIED.log`。

## 为什么没用 alembic

现在只有**一个库、一个环境**。alembic 最大的好处（迁移有版本号、有序、可回滚、多环境/多人
一致）暂时用不上；而它**管不了这个项目最痛的那件事**——它是按自己的迁移历史走的，不关心你
手上这个库实际偏离了多少。上面那两处漂移（模型缺 `vector_epoch` 的 server_default、模型里
根本没有 T54 那条索引）alembic 一个字都不会报，`schema_drift.py` 才会。

**什么时候该上**：出现第二个环境（测试/生产）或第二个人加入时；或者为了面试材料。

真要上，提前知道三个坑：

1. 用 `alembic init -t async migrations`。默认模板是同步驱动，而本项目是 asyncpg，直接用会连不上。
2. 已有库必须先 `alembic stamp head`「认账」，否则它会把所有已存在的表当缺失、想重建一遍。
3. `alembic revision --autogenerate` **生成不出部分唯一索引**这种带 `postgresql_where` 条件的
   对象（还有列类型变更、`server_default`），生成的 SQL 必须人工审一遍再 `upgrade`。
