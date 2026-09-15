"""Celery 任务队列：调研任务异步执行入口。"""
import asyncio
from celery import Celery
from celery.signals import worker_ready
from app.core.config import settings
from app.core.db import SessionLocal

celery_app = Celery("knowpilot", broker=settings.redis_url, backend=settings.redis_url)
celery_app.conf.update(
    task_serializer="json",
    accept_content=["json"],
    result_serializer="json",
    timezone="Asia/Shanghai",
    enable_utc=False,
    task_track_started=True,
    task_time_limit=180,
    task_soft_time_limit=150,
)

# 进程级常驻循环：避免 asyncio.run 每次新建/关闭循环，
# 导致 asyncpg/redis 连接跨循环复用报 "Event loop is closed"
_loop: asyncio.AbstractEventLoop | None = None


def _get_loop() -> asyncio.AbstractEventLoop:
    global _loop
    if _loop is None or _loop.is_closed():
        _loop = asyncio.new_event_loop()
        asyncio.set_event_loop(_loop)
    return _loop


@celery_app.task(name="research.run")
def research_task(task_id: str) -> None:
    """M1 占位：跑通"发起→执行→SSE 事件→完成"链路。
    M2 起改为调用 LangGraph 编排（services.agent.graph）。"""
    _get_loop().run_until_complete(_run(task_id))


@celery_app.task(name="document.parse", time_limit=900, soft_time_limit=840)
def parse_document_task(doc_id: str) -> None:
    _get_loop().run_until_complete(_parse_document(doc_id))


@worker_ready.connect
def _on_worker_ready(sender, **kwargs):
    _get_loop().run_until_complete(_recover_orphans())


async def _parse_document(doc_id: str) -> None:
    async with SessionLocal() as session:
        from app.models import Document
        doc = await session.get(Document, doc_id)
        if doc is None:
            print(f"[celery] 文档 {doc_id} 不存在，跳解析")
            return
        doc.status = "parsing"
        await session.commit()
        try:
            from app.core.storage import read_original
            from app.services.rag.parsers import extract_text
            from app.services.rag.ingest import reingest_text
            original = read_original(doc.object_key)
            text = extract_text(doc.file_name, original)
            if not text.strip():
                ext = doc.file_name.rsplit(".", 1)[-1].lower() if "." in doc.file_name else ""
                raise ValueError(
                    "未能提取到文本：疑似图片型/扫描 PDF（OCR 待接入，原文件已留存）"
                    if ext == "pdf" else "文件内容为空"
                )
            # 列 vector_epoch 是 NOT NULL、默认 0（见 migrations/0004）。
            # 不要写 `or 1`：0 是"还没解析过"的合法初始值，or 会把它替换成 1，导致首解析产出代次 2。
            new_epoch = doc.vector_epoch + 1
            doc.content = text
            chunk_count = reingest_text(doc.kb_id, doc_id, text, {"file_name": doc.file_name, "epoch": new_epoch})
            doc.status = "ready"
            doc.error = None
            doc.chunk_count = chunk_count
            doc.vector_epoch = new_epoch
        except Exception as e:
            doc.status = "failed"
            doc.error = str(e)[:500]

        await session.commit()


async def _recover_orphans() -> None:
    """上一 worker 进程已死：残留 pending/running 必为孤儿，置 interrupted 释放唯一索引占位。"""
    from app.core.db import SessionLocal
    from app.models import Task, Document
    from sqlalchemy import update
    async with SessionLocal() as session:
        task = await session.execute(
            update(Task)
            .where(Task.status.in_(["pending", "running"]))
            .values(status="interrupted",
                    error="worker 重启接管：原执行已中断，可点恢复重新执行")
        )

        doc = await session.execute(
            update(Document)
            .where(Document.status.in_(["parsing", "embedding"]))
            .values(status="failed",
                    error="worker 重启导致解析中断，请重新解析或重新上传")
        )

        await session.commit()
        print(
            f"[celery] 孤儿接管完成：{task.rowcount} 个 active 任务 → interrupted；{doc.rowcount} 个解析中文档 → failed")


async def _run(task_id: str) -> None:
    from app.core.db import SessionLocal
    from app.core.redis import publish_task_event
    from app.models import Task, Report, Citation, Document
    from app.services.agent.graph import build_graph
    from sqlalchemy import select
    from uuid import UUID

    try:
        async with SessionLocal() as session:
            task = await session.get(Task, task_id)
            if task is None or task.status == "cancelled":
                print(f"[celery] 任务 {task_id} 已取消，停止执行")
                return
            task.status = "running"
            kb_ids = list(task.kb_ids or [])
            rows = (await session.execute(
                select(Document.id, Document.vector_epoch).where(
                    Document.kb_id.in_([UUID(k) for k in kb_ids]))
            )).all()
            ready_doc_ids = [str(r[0]) for r in rows]
            ready_doc_epochs = {str(r[0]): int(r[1]) for r in rows}
            await session.commit()

        # 进图前的开工提示。step 用中性值 "start"：这里图还没开始跑，
        # 写成 "planner" 会被前端渲染成 planner 的事件，时间线上就排到了 probe 前面（踩坑 #98）。
        await publish_task_event(
            task_id,
            "agent_step",
            {"step": "start", "status": "running", "detail": "开始执行调研流程"}
        )

        graph = build_graph()

        result = {}
        async for step in graph.astream(
                {
                    "objective": task.objective,
                    "kb_ids": kb_ids or [],
                    "task_id": task_id,
                    "ready_doc_ids": ready_doc_ids,
                    "ready_doc_epochs": ready_doc_epochs,
                },
                stream_mode="updates"
        ):
            for node_name, update in step.items():
                if node_name == "probe":
                    flag = update.get("has_material")
                    count = update.get("material_count", 0)
                    if flag is True:
                        detail = f"知识库可用资料（{count} 条相关片段）"
                    elif flag is False:
                        detail = "知识库未检索到相关内容，仅凭模型知识拆章与撰写"
                    else:
                        detail = "资料预检未完成（未关联知识库或检索失败）"
                    await publish_task_event(
                        task_id=task_id,
                        event="agent_step",
                        data={"step": "probe", "status": "done", "detail": detail, "has_material": flag}
                    )

                elif node_name == "planner":
                    n = len(update.get("plan", []))
                    await publish_task_event(
                        task_id=task_id,
                        event="agent_step",
                        data={"step": "planner", "status": "done", "detail": f"拆解完成 {n} 个章节"}
                    )
                elif node_name == "retriever":
                    n = len(update.get("evidence", []))
                    await publish_task_event(
                        task_id=task_id,
                        event="agent_step",
                        data={"step": "retriever", "status": "done", "detail": f"资料检索完成（{n} 组证据）"}
                    )
                elif node_name == "synthesizer":
                    n = len(update.get("draft", {}).get("sections", []))
                    await publish_task_event(
                        task_id=task_id,
                        event="agent_step",
                        data={"step": "synthesizer", "status": "done", "detail": f"章节撰写完成（{n} 章），进入质检"}
                    )
                elif node_name == "critic":
                    await publish_task_event(
                        task_id=task_id,
                        event="agent_step",
                        data={"step": "critic", "status": "done", "detail": f"质检 {update.get('score')} 分"}
                    )
                result = {**result, **update}

                async with SessionLocal() as s:
                    t = await s.get(Task, task_id)
                    if t is None or t.status == "cancelled":
                        print(f"[celery] 任务 {task_id} 已取消，停止执行")
                        return

        draft = result["draft"]
        synth_errors = draft.pop("section_errors", None)  # synthesizer 降级章节（不进报告 JSON，并入 quality.issues）
        issues = list(result.get("issues", []))
        if synth_errors:
            issues = synth_errors + issues  # 章节失败原因置前，质检页可见
        draft["quality"] = {
            "reviewed": result.get("reviewed"),
            "score": result.get("score"),
            "passed": result.get("passed"),
            "issues": issues,
            "dimensions": result.get("dimensions") or [],
            "has_material": result.get("has_material"),
            "material_count": result.get("material_count"),
        }
        evidence_used = result.get("evidence_used", [])  # ① synthesizer 带回的引用元数据

        async with SessionLocal() as session:
            report = Report(task_id=task_id, title=draft["title"], content=draft)
            session.add(report)
            await session.flush()  # 先拿 report.id（INSERT ... RETURNING），不提交

            all_doc_ids = {d for sec in evidence_used for d in sec["document_ids"]}  # 全部被引用文档

            if all_doc_ids:
                docs = await session.scalars(select(Document).where(Document.id.in_(all_doc_ids)))
                title_map = {doc.id: doc.file_name for doc in docs}
                for evidence in evidence_used:
                    for doc_id in evidence["document_ids"]:
                        session.add(Citation(
                            report_id=report.id,
                            section_id=evidence["section_id"],
                            source_type="kb",
                            source_title=title_map.get(doc_id, str(doc_id)),
                            snippet=evidence["snippets"][0][:200] if evidence["snippets"] else None,
                            kb_id=kb_ids[0],
                            document_id=doc_id,
                        ))

            task = await session.get(Task, task_id)  # 本 session 里的可写对象（旧代码改的是外部已 detached 的 task）
            task.report_id = report.id
            task.status = "completed"
            # 报告 + 引用 + 任务状态一次提交：任一步失败则整体回滚，不留半成品
            await session.commit()

    except Exception as exc:
        async with SessionLocal() as session:
            t = await session.get(Task, task_id)
            if t is not None:
                t.status = "failed"
                t.error = str(exc)[:500]
                await session.commit()
        await publish_task_event(task_id, "task_failed", {"error": str(exc)})
        return

    await publish_task_event(task_id, "report_ready", {"report_id": report.id})
