"""调研任务接口：发起 / 查询 / 取消 / 干预 / SSE 事件流。"""
import json

from fastapi import APIRouter, Depends, HTTPException, status, Header
from fastapi.responses import StreamingResponse
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.exc import IntegrityError
from app.api.v1.deps import get_current_user
from app.core.db import get_db
from app.core.redis import publish_task_event, xrange_task_events, xread_task_events
from app.models import Project, Task, User, Report
from app.schemas import ResumeIn, TaskCreateIn, TaskOut
from app.services.task_queue import research_task

router = APIRouter(prefix="/tasks", tags=["tasks"])

TERMINAL_EVENTS = ("report_ready", "task_failed", "task_cancelled")
FINAL_STATUSES = ("completed", "failed", "cancelled", "interrupted")


async def _get_task(db: AsyncSession, task_id: str, user: User) -> Task:
    task = await db.get(Task, task_id)
    if task is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "任务不存在")
    project = await db.get(Project, task.project_id)
    if project is None or project.org_id != user.org_id:
        raise HTTPException(status.HTTP_403_FORBIDDEN, "无权访问")
    return task


@router.post("", response_model=TaskOut, status_code=status.HTTP_201_CREATED)
async def create_task(
        body: TaskCreateIn, user: User = Depends(get_current_user), db: AsyncSession = Depends(get_db)
):
    project = await db.get(Project, body.project_id)
    if project is None or project.org_id != user.org_id:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "项目不存在")

    active = await db.scalar(select(Task.id)
                             .where(
        Task.project_id == body.project_id,
        Task.status.in_(["pending", "running"]),
    ).limit(1))

    if active:
        raise HTTPException(status.HTTP_409_CONFLICT, "该项目已有进行中的调研任务，请等待完成后再发起")

    task = Task(
        kb_ids=body.kb_ids,
        project_id=body.project_id,
        objective=body.objective,
        title=body.title or body.objective[:30],
        created_by=user.id,
        status="pending",
    )
    db.add(task)
    try:
        await db.commit()
    except IntegrityError:
        await db.rollback()
        raise HTTPException(status.HTTP_409_CONFLICT, "该项目已有进行中的调研任务，请等待完成后再发起")
    await db.refresh(task)
    research_task.delay(task.id)
    return task


@router.get('', response_model=list[TaskOut])
async def list_tasks(db: AsyncSession = Depends(get_db), user: User = Depends(get_current_user)):
    # tasks = await db.execute(select(Task).filter(Task.created_by == user.id)).fetchall()
    result = await db.scalars(select(Task)
                              .join(Project, Task.project_id == Project.id)
                              .where(Project.org_id == user.org_id)
                              .order_by(Task.created_at.desc()))

    tasks = result.all()

    output = []

    for task in tasks:
        score = None
        passed = None

        if task.report_id:
            report = await db.get(Report, task.report_id)
            if report and report.content:
                quality = report.content.get("quality") or {}
                score = quality.get("score")
                passed = quality.get("passed")

        elapsed_seconds = None
        if task.status in ("completed", "failed", "cancelled"):
            elapsed_seconds = int(
                (task.updated_at - task.created_at).total_seconds()
            )

        item = TaskOut.model_validate(task).model_copy(
            update={
                "score": score,
                "passed": passed,
                "elapsed_seconds": elapsed_seconds,
            }
        )

        output.append(item)

    return output


@router.get("/{task_id}", response_model=TaskOut)
async def get_task(
        task_id: str, user: User = Depends(get_current_user), db: AsyncSession = Depends(get_db)
):
    return await _get_task(db, task_id, user)


@router.post("/{task_id}/cancel", response_model=TaskOut)
async def cancel_task(
        task_id: str, user: User = Depends(get_current_user), db: AsyncSession = Depends(get_db)
):
    task = await _get_task(db, task_id, user)
    if task.status in ("completed", "failed", "cancelled"):
        raise HTTPException(status.HTTP_409_CONFLICT, f"任务已处于 {task.status} 状态")
    task.status = "cancelled"
    await db.commit()
    await publish_task_event(task_id, "task_cancelled", {})
    await db.refresh(task)
    return task


@router.post("/{task_id}/resume", response_model=TaskOut)
async def resume_task(
        task_id: str,
        body: ResumeIn,
        user: User = Depends(get_current_user),
        db: AsyncSession = Depends(get_db),
):
    """人工干预后从断点继续（M2 接入 checkpointer 后生效）。"""
    task = await _get_task(db, task_id, user)
    if task.status != "interrupted":
        raise HTTPException(status.HTTP_409_CONFLICT, "任务未处于待干预状态")
    research_task.delay(task.id)  # M2: 携带 feedback 恢复 LangGraph 检查点
    task.status = "running"
    task.error = None
    await db.commit()
    await db.refresh(task)
    return task


@router.get("/{task_id}/events")
async def task_events(
        task_id: str,
        last_event_id: str | None = Header(default=None, alias="Last-Event-ID"),
):
    async def event_gen():
        # —— SSE 事件流：回放历史 → DB 兜底 → 实时续接 三段式 ——
        # 客户端带 Last-Event-ID 重连 → 只补游标之后漏掉的事件；首次订阅 → 全量回放
        from app.core.db import SessionLocal
        from app.models import Task

        # 段① 查 DB 任务状态：供段③兜底判断（stream key 过期后，DB 是唯一事实源）
        async with SessionLocal() as session:
            task = await session.get(Task, task_id)

        # 段② 回放历史：XRANGE 开区间取游标之后全部事件；每条带 id: 行（前端续传游标）
        cursor = last_event_id or '-'  # 无游标（None）→ '-' 全量回放
        history = await xrange_task_events(task_id, after_id=cursor)
        for entry_id, event, data in history:
            yield f"id: {entry_id}\nevent: {event}\ndata: {json.dumps(data, ensure_ascii=False)}\n\n"
            if event in TERMINAL_EVENTS:
                return  # 历史已含终态事件 → 直接关流

        # 段③ DB 兜底：任务已终态但历史未遇终态事件（stream 过期 / 游标已越过终态）
        #       → 补发快照（completed 带 report_id 供前端跳转）后关流，避免掉进段④永久悬挂
        if task is not None and task.status in FINAL_STATUSES:
            snapshot = {"status": task.status}
            if task.report_id:
                snapshot["report_id"] = task.report_id
            yield f"event: task_status\ndata: {json.dumps(snapshot, ensure_ascii=False)}\n\n"
            return

        # 段④ 实时续接：XREAD 阻塞等新事件（BLOCK 超时返回 None → continue 保持连接）
        # 游标：历史非空 → 从最后一条续（开区间无缝）；空 → 重连沿用原游标、首订用 $（只等未来）
        _cursor = history[-1][0] if history else (last_event_id or "$")
        while True:
            batch = await xread_task_events(task_id, cursor=_cursor, block_ms=5000)
            if batch is None:
                continue  # 5s 无新消息 → 继续等，不能 break（break 会关流）
            for entry_id, event, data in batch:
                yield f"id: {entry_id}\nevent: {event}\ndata: {json.dumps(data, ensure_ascii=False)}\n\n"
                if event in TERMINAL_EVENTS:
                    return  # 终态事件到达 → 关流
            _cursor = batch[-1][0]  # 推进游标：XREAD 严格大于语义保证下一轮不重不漏

    return StreamingResponse(
        event_gen(),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no", "Connection": "keep-alive"},
    )
