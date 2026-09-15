"""报告接口：详情 / 引用 / 章节追问 / 导出。"""
from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import PlainTextResponse
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.v1.deps import get_current_user
from app.core.db import get_db
from app.models import Citation, Project, Report, Task, User
from app.schemas import CitationOut, ReportOut, SectionRevisitIn
from urllib.parse import quote

router = APIRouter(prefix="/reports", tags=["reports"])


async def _get_report(db: AsyncSession, report_id: str, user: User) -> Report:
    report = await db.get(Report, report_id)
    if report is None:
        raise HTTPException(404, "报告不存在")
    task = await db.get(Task, report.task_id)
    project = await db.get(Project, task.project_id)
    if project.org_id != user.org_id:
        raise HTTPException(403, "无权访问")
    return report


@router.get("/{report_id}", response_model=ReportOut)
async def get_report(report_id: str, user: User = Depends(get_current_user), db: AsyncSession = Depends(get_db)):
    return await _get_report(db, report_id, user)


@router.get("/{report_id}/citations", response_model=list[CitationOut])
async def list_citations(report_id: str, user: User = Depends(get_current_user), db: AsyncSession = Depends(get_db)):
    await _get_report(db, report_id, user)
    rows = await db.scalars(select(Citation).where(Citation.report_id == report_id).order_by(Citation.section_id, Citation.source_title, Citation.document_id)  )
    return rows.all()


@router.post("/{report_id}/sections/{section_id}/revisit")
async def revisit_section(
    report_id: str,
    section_id: str,
    body: SectionRevisitIn,
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """章节级追问：M3 接入局部检索修订流程。"""
    report = await _get_report(db, report_id, user)
    return {"status": "accepted", "report_id": report.id, "section_id": section_id, "pending": True}


@router.get("/{report_id}/export")
async def export_report(report_id: str, user: User = Depends(get_current_user), db: AsyncSession = Depends(get_db)):
    """导出 Markdown 中间表示（Word/PDF 由渲染器消费，M3 实现）。"""
    report = await _get_report(db, report_id, user)
    title = report.title
    md = [f"# {title}", ""]
    for section in report.content.get("sections", []):
        md.append(f"## {section.get('title', '')}")
        md.append(section.get("content", ""))
        md.append("")
    citations = await db.scalars(select(Citation).where(Citation.report_id == report_id))
    rows = citations.all()
    if rows:
        md.append("## 参考来源")
        md.append("")
        for c in rows:
            md.append(f"- {c.source_title}" + (f"（{c.source_url}）" if c.source_url else ""))

    text = "\n".join(md)
    filename = quote(f"{title}.md")  # 中文文件名必须 percent-encode
    return PlainTextResponse(
        text,
        media_type="text/markdown; charset=utf-8",
        headers={"Content-Disposition": f"attachment; filename*=UTF-8''{filename}"},
    )
