# 知研 KnowPilot — AI 研究工作台

把"检索 → 阅读 → 分析 → 成稿"的调研闭环交给 AI 智能体。

- PRD：`../PRD-知研-AI研究工作台.md`
- TDD：`../TDD-知研-技术设计文档.md`

## 快速启动

```bash
cp .env.example .env     # 填入 DEEPSEEK_API_KEY / BOCHA_API_KEY 等
docker compose up -d     # 拉起 PG / Redis / Milvus / MinIO / backend / worker / frontend
```

- 后端 API：http://localhost:8000/docs （Swagger）
- 前端：http://localhost:5173

## 目录

```
knowpilot/
├── docker-compose.yml
├── backend/    FastAPI + Celery + LangGraph + RAG
└── frontend/   Vue 3 + Vite + TS + Naive UI
```

## 里程碑

M1 基建（当前）→ M2 任务闭环 → M3 报告体验 → M4 知识库 → M5 打磨
