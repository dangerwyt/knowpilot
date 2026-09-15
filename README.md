# 知研 KnowPilot — AI 调研工作台

给一个调研目标，自动跑完「资料预检 → 拆章 → 检索 → 并行撰写 → 质检 → 不达标带意见重写」，
交付一份带引用溯源的 Markdown 报告，全过程实时推到前端时间线。

后端 FastAPI + LangGraph + Milvus + PostgreSQL + Celery + Redis ｜ 前端 Vue 3 + Element Plus + TypeScript

## 文档

| 文档 | 内容 |
|---|---|
| [项目介绍](docs/项目介绍-知研KnowPilot.md) | 定位 / 核心特性 / 架构图 / 调研链路 / 数据模型 / API 全览（27 个端点） |
| [能力对比与功能演进建议](docs/能力对比与功能演进建议.md) | 与同类 RAG 项目的能力对比、可加功能优先级、已知问题 |
| [PRD](docs/PRD-知研-AI研究工作台.md) | 产品需求：用户、场景、功能地图 |
| [TDD](docs/TDD-知研-技术设计文档.md) | 技术设计：分层、数据模型、关键决策 |
| [踩坑记录](docs/踩坑记录.md) | 100 条：现象 / 根因 / 修法，按 8 类归档 |
| [backend/playground](backend/playground/) | 40 个可证伪的验收脚本与探针工具 |

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
├── frontend/         Vue 3 + Element Plus + TypeScript
└── docs/             PRD / TDD / 项目介绍 / 踩坑记录
```

## 里程碑

M1 基建 ✅ → M2 任务闭环 ✅ → M3 报告体验 ✅ → M4 知识库 ✅ → **M5 打磨（收尾中）**
