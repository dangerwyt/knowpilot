"""T65-B 巡检：PG chunk_count vs Milvus 实际块数对账 + --fix 修复。

用法（backend/ 目录下）：
    python playground/drift_check.py        # 只读报告，不写任何数据
    python playground/drift_check.py --fix  # 修复漂移（仅 ready 且 content 非空）
"""
import asyncio
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from sqlalchemy import select

from app.core.db import SessionLocal
from app.models import Document
from app.services.rag.milvus_client import inspect_chunks_by_document


def report_line(doc, mv_count: int | None, dups: list[int] | None) -> str:
    """拼一行输出。mv_count=None 表示 Milvus 查询失败（服务挂/异常）。"""
    # TODO-③
    if mv_count is None:
        return f"{doc.file_name:<24} {doc.status:<6} pg={doc.chunk_count}  milvus=ERR  diff=ERR"
    diff = doc.chunk_count - mv_count
    flag = " <-漂移" if diff !=0 else ""
    if dups:
        flag += "  <-重复 seq=[" + ",".join(str(s) for s in dups) + "]"
    return f"{doc.file_name:<24} {doc.status:<6} pg={doc.chunk_count}  milvus={mv_count}  diff={diff}{flag}"


async def fix_document(db, doc) -> tuple[int, int] | None:
    """修复单个漂移文档，返回 (旧 PG 值, 修复后真实块数)；content 缺失返回 None。"""
    if not doc.content:
        return None
    from app.services.rag.ingest import reingest_text
    before = doc.chunk_count
    after = reingest_text(doc.kb_id, doc.id, doc.content, {"file_name": doc.file_name, "epoch": doc.vector_epoch})
    doc.chunk_count = after
    await db.commit()
    return before, after


async def main() -> int:
    fix = "--fix" in sys.argv[1:]
    async with SessionLocal() as db:
        res = await db.execute(select(Document))
        docs = res.scalars().all()
        rows = []
        for doc in docs:
            if doc.status != "ready":
                print(f"跳过（{doc.status}）: {doc.file_name}")
                continue
            try:
                mv_count, dups = inspect_chunks_by_document(doc.id)
            except Exception as e:
                print(f"[drift] Milvus 查询失败（该行标 None，跳过）: {doc.file_name}: {e}")
                mv_count, dups = None, []
            rows.append({"doc": doc, "mv_count": mv_count, "dups": dups})

        for row in rows:
            doc, mv = row["doc"], row["mv_count"]
            print(report_line(doc, mv, row["dups"]))
            if mv is None:
                continue
            diff = doc.chunk_count - mv
            if fix and (diff != 0 or row["dups"]):
                try:
                    result = await fix_document(db, doc)
                except Exception as e:
                    print(f"修复失败（可重跑 --fix）: {doc.file_name}: {e}")
                    continue
                if result is None:
                    print(f"跳过修复（content 缺失，需 reparse 原文件）: {doc.file_name}")
                    continue
                row["mv_count"] = result[1]
                print(f"修复 {doc.file_name}: {result[0]} -> {result[1]}")

        drift = [row for row in rows if row["mv_count"] is not None and row["mv_count"] != row["doc"].chunk_count]
        dup_rows = [row for row in rows if row["dups"]]
        skipped = len(docs) - len(rows)
        print("-" * 60)
        print(
            f"ready={len(rows)}  skipped={skipped}  drift={len(drift)} dup={len(dup_rows)} mode={'fix' if fix else 'readonly'}")
        if not fix and (drift or dup_rows):
            return 1
    return 0


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
