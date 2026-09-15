# app/api/v1/endpoints/documents.py
from fastapi import APIRouter, Depends
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from app.core.db import get_db
from app.api.v1.deps import get_current_user
from app.models import Document, KnowledgeBase, User
from app.schemas import DocumentOut

router = APIRouter(prefix="/documents", tags=["documents"])

@router.get("", response_model=list[DocumentOut])
async def list_all_documents(
    kb_id: str | None = None,
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):

    stmt = (select(Document)
            .join(KnowledgeBase, Document.kb_id == KnowledgeBase.id)
            .where(KnowledgeBase.org_id == user.org_id))

    if kb_id:
        stmt = stmt.where(Document.kb_id == kb_id)
    stmt = stmt.order_by(Document.created_at.desc())

    return (await db.scalars(stmt)).all()
