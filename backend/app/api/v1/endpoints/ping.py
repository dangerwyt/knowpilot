from fastapi import APIRouter
from datetime import datetime

router = APIRouter(prefix="/ping", tags=["ping"])

@router.get("")
async def ping():
    return {"pong": True, "time": datetime.now().timestamp()}
