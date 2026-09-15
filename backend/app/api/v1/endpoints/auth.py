"""认证接口：注册 / 登录 / 当前用户。"""
from fastapi import APIRouter, Depends, HTTPException, status
from passlib.context import CryptContext
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.v1.deps import create_token, get_current_user
from app.core.db import get_db
from app.models import Org, User
from app.schemas import LoginIn, RegisterIn, TokenOut, UserOut

router = APIRouter(prefix="/auth", tags=["auth"])

pwd_context = CryptContext(schemes=["bcrypt"], deprecated="auto")


def _user_out(user: User) -> UserOut:
    return UserOut(id=user.id, email=user.email, name=user.name, role=user.role)


@router.post("/register", response_model=TokenOut, status_code=status.HTTP_201_CREATED)
async def register(body: RegisterIn, db: AsyncSession = Depends(get_db)):
    exists = await db.scalar(select(User).where(User.email == body.email))
    if exists:
        raise HTTPException(status.HTTP_409_CONFLICT, "邮箱已注册")
    org = Org(name=body.email.split("@")[0])
    db.add(org)
    await db.flush()
    user = User(
        email=body.email,
        password_hash=pwd_context.hash(body.password),
        name=body.name,
        org_id=org.id,
        role="owner",
    )
    db.add(user)
    await db.commit()
    await db.refresh(user)
    return TokenOut(token=create_token(user.id), user=_user_out(user))


@router.post("/login", response_model=TokenOut)
async def login(body: LoginIn, db: AsyncSession = Depends(get_db)):
    user = await db.scalar(select(User).where(User.email == body.email))
    if user is None or not pwd_context.verify(body.password, user.password_hash):
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "邮箱或密码错误")
    return TokenOut(token=create_token(user.id), user=_user_out(user))


@router.get("/me", response_model=UserOut)
async def me(user: User = Depends(get_current_user)):
    return _user_out(user)
