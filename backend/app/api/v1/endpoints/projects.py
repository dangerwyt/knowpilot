"""项目空间接口。"""
from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.v1.deps import get_current_user
from app.core.db import get_db
from app.models import Project, User
from app.schemas import ProjectIn, ProjectOut

router = APIRouter(prefix="/projects", tags=["projects"])


@router.get("", response_model=list[ProjectOut])
async def list_projects(
    user: User = Depends(get_current_user), db: AsyncSession = Depends(get_db)
):
    rows = await db.scalars(select(Project).where(Project.org_id == user.org_id).order_by(Project.created_at.desc()))
    return rows.all()


@router.post("", response_model=ProjectOut, status_code=status.HTTP_201_CREATED)
async def create_project(
    body: ProjectIn, user: User = Depends(get_current_user), db: AsyncSession = Depends(get_db)
):
    project = Project(org_id=user.org_id, name=body.name, description=body.description, created_by=user.id)
    db.add(project)
    await db.commit()
    await db.refresh(project)
    return project
