-- 0001 · T54：同一项目下只允许一个"活跃"任务（部分唯一索引）
--
-- 目的：     挡住"同项目并发触发两个调研任务"——两个窗口同时点，第二个任务
--            会 pending 卡住、占着单 worker（`--pool=solo`）。在 DB 层直接堵死，
--            而不是靠前端按钮禁用。
-- 影响表：   tasks（新增索引 uq_tasks_active_per_project）
-- 适用前提：tasks 表已存在；且当前没有"同项目下同时存在多条 pending/running"的行
--            （有的话建索引会失败，先清理历史脏数据）。
-- 幂等性：   否。重复执行会报 index already exists。
-- 执行：     cd backend && ./.venv/Scripts/python.exe migrations/apply.py 0001_t54_tasks_active_unique.sql
--
-- 回滚：
--   DROP INDEX IF EXISTS uq_tasks_active_per_project;
--
-- 备注（2026-09-14 补录）：本文件是事后补写的——当时直接在库里执行了 SQL，
--   模型没同步，导致"从零建库"会丢掉这条约束。现已同时在
--   app/models/__init__.py 的 Task.__table_args__ 里声明（两边保持一致）。

CREATE UNIQUE INDEX uq_tasks_active_per_project
    ON tasks (project_id)
    WHERE status IN ('pending', 'running');
