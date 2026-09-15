"""知研 KnowPilot 后端入口。"""
import logging
from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from app.api.v1.router import api_router
from app.core.config import settings
from app.models import init_db
from app.services.rag.milvus_client import ensure_collection

logging.basicConfig(level=logging.INFO)


@asynccontextmanager
async def lifespan(_: FastAPI):
    try:
        await init_db()
    except Exception as exc:  # noqa: BLE001
        logging.warning("数据库建表失败（可能未就绪）: %s", exc)
    ensure_collection()  # 幂等，Milvus 未就绪时记录告警，M4 任务会重试
    yield


app = FastAPI(title=settings.app_name, version=settings.version or "0.1.0", lifespan=lifespan)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],  # 开发期全放行；上线收紧
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)
app.include_router(api_router, prefix="/api/v1")

