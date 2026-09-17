# 知研 KnowPilot — AI 调研工作台

给一个调研目标，自动跑完「资料预检 → 拆章 → 检索 → 并行撰写 → 质检 → 不达标带意见重写」，
交付一份带引用溯源的 Markdown 报告，全过程实时推到前端时间线。

后端 FastAPI + LangGraph + Milvus + PostgreSQL + Celery + Redis ｜ 前端 Vue 3 + Element Plus + TypeScript

## 核心特性

```
START → probe → planner → retriever → synthesizer → critic ─┬→ END
                                        ▲                    │
                                        └───── rewrite ──────┘
```

**可量化的质检（不是"感觉还行"）**
评审模型按 完整度 / 相关度 / 事实性 / 结构规范 **四维各 25 分**打分，通过线 70；
不通过则把评审意见回灌作者重写（`MAX_RETRIES = 2`）。
**总分由代码求和，不由模型自报**——避免"四项打对了但总分算错"，模型自报的总分只打日志作对照。

**资料预检：先问"有没有料"再决定怎么写**
提交后先检索一次（阈值 0.41）拿三态结论 —— 有资料 / 明确没检索到 / 未关联知识库。
没资料时如实告知（前端弹确认框）而不是硬编；规划阶段**不硬拆**资料未覆盖的章节。

**并发约束下沉到数据库**
部分唯一索引 `(project_id) WHERE status IN ('pending','running')`，
把"一个项目同时只能有一个活跃任务"从应用层的"尽量保证"变成数据库层的硬约束——
根治了双窗口同时提交时"第二个任务卡在 pending 抢不到 worker"。

**SSE 断点续传**
事件写入 Redis Streams（TTL 600s），前端重连带 `Last-Event-ID` 先重放历史再续接新事件；
刷新页面 / 断网重连不丢进度。

**四处降级路径 —— 跑不通也是一条路，不是崩**
质检服务不可用则跳过质检交付并写明原因；单章失败降级占位、其余照常；
全部章节失败直接置 `failed`，**不交付空报告**。

验收脚本都在 [`backend/playground/`](backend/playground/)：判据建在形态稳定的产物上
（API 数组长度、事件计数、DB 列），不依赖 LLM 自由文本，且每条都验证过"能 FAIL"。

## 快速启动

### 1. 配置

```bash
cp .env.example .env                   # 根目录：给 docker compose 用（PG 密码、MinIO 等）
cp backend/.env.example backend/.env   # 后端：填 DASHSCOPE_API_KEY / DEEPSEEK_API_KEY
```

两处 `.env` 的密码要一致（根目录的 `POSTGRES_PASSWORD` ↔ 后端 `DATABASE_URL` 里的密码）。

### 2. 起基础设施

```bash
docker compose up -d postgres redis milvus
```

> Milvus 会自动带上它依赖的 etcd 与 MinIO，不用单独起。

### 3. 起后端

```bash
cd backend
pip install -r requirements.txt
python -c "import asyncio; from app.models import init_db; asyncio.run(init_db())"   # 首次建表
python -m uvicorn app.main:app --reload --port 8000
```

- 健康检查：<http://127.0.0.1:8000/api/v1/health>
- 接口文档：<http://127.0.0.1:8000/docs>

### 4. 起 Celery Worker（**必须单进程**）

```bash
cd backend
celery -A app.services.task_queue:celery_app worker --pool=solo -l info
```

> Windows 上不加 `--pool=solo` 会起多进程，消息被争抢会导致任务莫名丢失或卡住。
> 改了后端代码要重启 worker（uvicorn 有 `--reload` 会自热重载，worker 不会）。

### 5. 起前端

```bash
cd frontend
pnpm install
pnpm dev            # http://127.0.0.1:5173，/api 代理到 :8000
```

### 或者：全交给 compose

```bash
docker compose up -d      # 连 backend / worker / frontend 一起起
```

省事，但改代码调试不如本机跑方便。
compose 里的 worker 已同样带上 `--pool=solo`——容器内默认走 prefork（本机 16 核＝16 个进程），
会同时消费一条队列，把"单 worker 串行"这个前提挖掉。

## 目录

```
knowpilot/
├── docker-compose.yml
├── backend/          FastAPI + Celery + LangGraph + RAG
│   ├── app/          接口 / 服务 / 模型 / 配置
│   ├── migrations/   手写 SQL 迁移（每条含回滚段）+ apply.py
│   └── playground/   验收脚本与探针工具
└── frontend/         Vue 3 + Element Plus + TypeScript
```

> 内部文档（PRD / TDD / 踩坑记录 / 部署方案）不在本仓库，不随代码公开。
> 仓库里的 `backend/playground/` 是可直接读的验收脚本 —— 判据怎么写、
> 怎么保证 PASS 不是恒真，都在里面。

## 里程碑

M1 基建 ✅ → M2 任务闭环 ✅ → M3 报告体验 ✅ → M4 知识库 ✅ → **M5 打磨（收尾中）**
