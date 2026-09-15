"""知识库接口：创建 / 上传文档 / 状态查询。"""
from fastapi import APIRouter, Depends, HTTPException, UploadFile, File, status
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select, func, and_

from app.api.v1.deps import get_current_user
from app.core.db import get_db
from app.models import Document, KnowledgeBase, User
from app.schemas import DocumentOut, DocumentDetailOut, KBCreateIn, KBOut, ChunkOut
from app.services.task_queue import parse_document_task

router = APIRouter(prefix="/knowledge-bases", tags=["knowledge-bases"])

def _to_kb_out(kb: KnowledgeBase, cnt: int = 0) -> KBOut:
    return KBOut(
        id=kb.id,
        name=kb.name,
        embedding_model=kb.embedding_model,
        chunk_strategy=kb.chunk_strategy,
        doc_count=cnt,
        created_at=kb.created_at,
    )


@router.get("", response_model=list[KBOut])
async def list_kbs(user: User = Depends(get_current_user), db: AsyncSession = Depends(get_db)):
    rows = (await db.execute(
        select(KnowledgeBase, func.count(Document.id))
        .outerjoin(Document, and_(
            Document.kb_id == KnowledgeBase.id,
            Document.status == "ready",
        ))
        .where(KnowledgeBase.org_id == user.org_id)
        .group_by(KnowledgeBase.id)
    )).all()
    return [
        _to_kb_out(kb, cnt)
        for kb, cnt in rows
    ]


@router.post("", response_model=KBOut, status_code=status.HTTP_201_CREATED)
async def create_kb(
        body: KBCreateIn, user: User = Depends(get_current_user), db: AsyncSession = Depends(get_db)
):
    kb = KnowledgeBase(org_id=user.org_id, name=body.name, chunk_strategy=body.chunk_strategy or {})
    db.add(kb)
    await db.commit()
    await db.refresh(kb)
    return _to_kb_out(kb, 0)


@router.delete("/{kb_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_kb(
        kb_id: str,
        user: User = Depends(get_current_user),
        db: AsyncSession = Depends(get_db)
):
    kb = await db.get(KnowledgeBase, kb_id)
    if kb is None or kb.org_id != user.org_id:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "知识库不存在")
    rows = (await db.execute(
        select(Document.id, Document.object_key).where(Document.kb_id == kb_id)
    )).all()
    # ① Milvus 向量（先清向量，DB 行删了但 Milvus 崩会留孤儿向量）
    try:
        from app.services.rag.milvus_client import delete_by_document

        for doc_id, _ in rows:
            delete_by_document(doc_id)
    except Exception as e:  # noinspection PyBroadException
        # Milvus 清理尽力而为：基础设施故障不应阻断删 KB；留痕便于日后排查孤儿向量
        print(f"[kb] 删 KB 时 Milvus 向量清理失败（忽略）: {e}")

    # ② 原文件（object_key 记在 documents 行上，DB 行删了就找不回文件了）
    try:
        from app.core.storage import delete_original
        for _, object_key in rows:
            delete_original(object_key)
    except Exception as e:
        print(f"[kb] 删 KB 时原文件清理失败（忽略）: {e}")

    from sqlalchemy import delete
    # ③ documents 行
    await db.execute(delete(Document).where(Document.kb_id == kb_id))
    # ④ kb 行
    await db.execute(delete(KnowledgeBase).where(KnowledgeBase.id == kb_id))
    await db.commit()


@router.post("/{kb_id}/documents", response_model=DocumentOut, status_code=status.HTTP_201_CREATED)
async def upload_document(
        kb_id: str,
        file: UploadFile = File(...),
        user: User = Depends(get_current_user),
        db: AsyncSession = Depends(get_db),
):
    from app.services.rag.parsers import SUPPORTED_EXTS
    from app.core.storage import save_original

    if not file.filename:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "文件名不能为空")

    ext = file.filename.rsplit(".", 1)[-1].lower() if "." in file.filename else ""

    if ext not in SUPPORTED_EXTS:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, f"不支持的文件类型: {ext}")

    kb = await db.get(KnowledgeBase, kb_id)
    if kb is None or kb.org_id != user.org_id:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "知识库不存在")

    raw = await file.read()

    # 建 Document 记录
    doc = Document(
        kb_id=kb_id,
        file_name=file.filename or "unnamed",
        file_type=file.content_type or "text/plain",
        size_bytes=len(raw),
        status="parsing",
    )
    db.add(doc)
    await db.commit()
    await db.refresh(doc)

    rel = save_original(doc.kb_id, doc.id, ext, raw)
    doc.object_key = rel

    await db.commit()
    parse_document_task.delay(doc.id)
    return doc


@router.get("/{kb_id}/documents", response_model=list[DocumentOut])
async def list_documents(
        kb_id: str, user: User = Depends(get_current_user), db: AsyncSession = Depends(get_db)
):
    kb = await db.get(KnowledgeBase, kb_id)
    if kb is None or kb.org_id != user.org_id:
        raise HTTPException(404, "知识库不存在")
    rows = await db.scalars(select(Document).where(Document.kb_id == kb_id).order_by(Document.created_at.desc()))
    return rows.all()


@router.get("/documents/{doc_id}", response_model=DocumentDetailOut)
async def document_status(
        doc_id: str, user: User = Depends(get_current_user), db: AsyncSession = Depends(get_db)
):
    doc = await db.get(Document, doc_id)
    if doc is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "文档不存在")
    kb = await db.get(KnowledgeBase, doc.kb_id)
    if kb is None or kb.org_id != user.org_id:
        raise HTTPException(status.HTTP_403_FORBIDDEN, "无权访问")

    detail = DocumentDetailOut.model_validate(doc)
    try:
        from app.services.rag.milvus_client import list_chunks_by_document
        detail.chunks = [ChunkOut(**c) for c in list_chunks_by_document(doc_id)]

    except Exception as e:
        print(f"[kb] Milvus 分块查询失败（忽略）: {e}")

    return detail


@router.delete("/{kb_id}/documents/{doc_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_document(
        kb_id: str,
        doc_id: str,
        user: User = Depends(get_current_user),
        db: AsyncSession = Depends(get_db),
):
    kb = await db.get(KnowledgeBase, kb_id)
    if kb is None or kb.org_id != user.org_id:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "知识库不存在")
    doc = await db.get(Document, doc_id)
    if doc is None or str(doc.kb_id) != kb_id:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "文档不存在")

    try:
        from app.services.rag.milvus_client import delete_by_document
        delete_by_document(doc_id)
    except Exception as e:  # noinspection PyBroadException
        # Milvus 清理尽力而为：基础设施故障不应阻断删文档；留痕便于日后排查孤儿向量
        print(f"[kb] 删文档 时 Milvus 向量清理失败（忽略）: {e}")

    from app.core.storage import delete_original
    delete_original(doc.object_key)
    await db.delete(doc)
    await db.commit()


@router.post("/{kb_id}/documents/{doc_id}/reparse", status_code=status.HTTP_204_NO_CONTENT)
async def reparse_document(
        kb_id: str,
        doc_id: str,
        user: User = Depends(get_current_user),
        db: AsyncSession = Depends(get_db),
):
    kb = await db.get(KnowledgeBase, kb_id)
    if kb is None or kb.org_id != user.org_id:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "知识库不存在")
    doc = await db.get(Document, doc_id)
    if doc is None or str(doc.kb_id) != kb_id:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "文档不存在")

    if not doc.object_key:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "原始文件缺失，无法重新解析，请重新上传")

    if doc.status == "parsing":
        raise HTTPException(status.HTTP_409_CONFLICT, "该文档正在解析中，请稍候")

    from sqlalchemy import delete
    from app.models import Citation
    await db.execute(delete(Citation).where(Citation.document_id == doc_id))

    doc.status = "parsing"
    doc.error = None
    await db.commit()
    parse_document_task.delay(doc_id)
