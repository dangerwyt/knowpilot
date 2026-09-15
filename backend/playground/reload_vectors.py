"""一次性刷新存量向量：让所有块都带上当前代次（T72 配套）。

为什么需要：T72 给检索加了「按代次过滤」条件，而 Milvus 里没有代次键的旧块
在 `metadata["epoch"] == N` 下判定为 false → 会被全部排除，检索直接变空。
所以必须把存量块重灌一遍，让它们带上 `metadata["epoch"]`。

做什么：对每个 status='ready' 且 content 非空的文档，走一遍 `reingest_text`
（先算 → 再删 → 后写），写入 `metadata = {"file_name": …, "epoch": 当前代次}`，
并用返回的块数刷新 `documents.chunk_count`。**不改 `vector_epoch`**（代次由业务代码管）。

用法：
    python playground/reload_vectors.py            # 演练，只看会做什么
    python playground/reload_vectors.py --yes      # 真的执行
"""
import asyncio
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from sqlalchemy import text  # noqa: E402

DO_IT = "--yes" in sys.argv[1:]


async def main() -> None:
    from app.core.db import SessionLocal
    from app.services.rag.ingest import reingest_text
    from app.services.rag.milvus_client import inspect_chunks_by_document

    async with SessionLocal() as session:
        has_col = (await session.execute(text(
            "select count(*) from information_schema.columns "
            "where table_name='documents' and column_name='vector_epoch'"
        ))).scalar()
        if not has_col:
            print("× documents.vector_epoch 列不存在 —— 先做 T72-1（加列）再跑本脚本")
            return

        rows = (await session.execute(text(
            "select id, kb_id, file_name, content, vector_epoch, chunk_count "
            "from documents where status='ready' and content is not null order by created_at"
        ))).all()

    if not rows:
        print("没有可刷新的文档（status='ready' 且 content 非空）")
        return

    print(f"待刷新 {len(rows)} 个文档 ｜ 模式：{'执行' if DO_IT else '演练（加 --yes 才真跑）'}")
    print("-" * 68)

    total_before = total_after = 0
    for doc_id, kb_id, file_name, content, epoch, pg_count in rows:
        doc_id, kb_id, epoch = str(doc_id), str(kb_id), int(epoch or 1)
        before, _ = inspect_chunks_by_document(doc_id)
        total_before += before
        print(f"  {file_name:<26} epoch={epoch}  Milvus {before} 行  pg.chunk_count={pg_count}")
        if not DO_IT:
            continue
        try:
            n = reingest_text(kb_id, doc_id, content, {"file_name": file_name, "epoch": epoch})
            after, dups = inspect_chunks_by_document(doc_id)
            total_after += after
            flag = "" if after == n else f"  ⚠ 残留：写入 {n} 但库里有 {after} 行（Milvus 删除不生效）"
            if dups:
                flag += f"  重复 seq={dups}"
            async with SessionLocal() as session:
                await session.execute(
                    text("update documents set chunk_count=:n where id=:i"), {"n": n, "i": doc_id}
                )
                await session.commit()
            print(f"    -> 重灌完成：{n} 行{flag}")
        except Exception as exc:  # noqa: BLE001
            print(f"    -> 失败（可重跑）：{type(exc).__name__}: {str(exc)[:140]}")

    print("-" * 68)
    if DO_IT:
        print(f"Milvus 行数：{total_before} -> {total_after}")
        print("接着跑：python playground/drift_check.py   （期望 drift=0 dup=0）")
    else:
        print(f"演练结束，未改动任何数据（当前 Milvus 合计 {total_before} 行）")


asyncio.run(main())
