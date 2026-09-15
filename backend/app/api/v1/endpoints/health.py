
from fastapi import APIRouter
from app.core.config import settings

router = APIRouter(prefix="/health", tags=["health"])

@router.get("")
async def health():
    return {"status": "ok", "app": settings.app_name, "version": settings.version}
