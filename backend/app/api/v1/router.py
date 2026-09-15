"""API v1 路由聚合。"""
from fastapi import APIRouter

from app.api.v1.endpoints import auth, kb, projects, reports, tasks, ping, documents

from app.api.v1.endpoints import health

api_router = APIRouter()
api_router.include_router(health.router)
api_router.include_router(auth.router)
api_router.include_router(projects.router)
api_router.include_router(tasks.router)
api_router.include_router(kb.router)
api_router.include_router(reports.router)
api_router.include_router(ping.router)
api_router.include_router(documents.router)
