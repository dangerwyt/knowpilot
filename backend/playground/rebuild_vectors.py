"""重建 Milvus collection 并从 PG 原文重灌 —— 清掉「删不掉的残留块」。

为什么需要这个工具（踩坑 #61 的延伸）：
    本环境 Milvus 的删除不可靠，且不只「删不掉」——实测还出现**延迟复活**：
    重灌时 `delete → flush → query` 确认只剩 1 行，过一阵再看，被删的旧块又出现了。
    反复重灌因此会累积**同代次重复块**（epoch 相同 → T72 的代次过滤挡不住 →
    检索会把同一段内容重复召回，白占 top_k）。这种状态无法用 delete 收干净，
    唯一可靠的办法是 drop collection 后重建。

做什么：
    1. 打印重建前状态（每文档行数 + epoch 分布）
    2. drop collection → ensure_collection() 按 schema 重建
    3. 对每个 status='ready' 且 content 非空的文档，从 PG 原文重灌，
       metadata 带 {"file_name": …, "epoch": 该文档当前代次}
    4. 重建后**立即校验**，再**等一会儿复校**（专门捕捉延迟复活）

用法（在 backend/ 目录下）：
    ./.venv/Scripts/python.exe playground/rebuild_vectors.py           # 演练：只打印，不动数据
    ./.venv/Scripts/python.exe playground/rebuild_vectors.py --yes     # 真跑
"""
import asyncio
import sys
import time
from collections import defaultdict
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

DO_IT = "--yes" in sys.argv[1:]
RECHECK_DELAY = 15  # 秒：等待后再看一眼，捕捉「延迟复活」


def snapshot() -> dict[str, list[tuple]]:
    """每文档 -> [(seq, epoch)]，按 file_name 归组。"""
    from app.core.config import settings
    from pymilvus import MilvusClient

    client = MilvusClient(uri=settings.milvus_uri)
    if not client.has_collection(settings.milvus_collection):
        return {}
    rows = client.query(
        collection_name=settings.milvus_collection,
        filter="seq >= 0",
        output_fields=["document_id", "seq", "metadata"],
        limit=16384,
    )
    g: dict[str, list[tuple]] = defaultdict(list)
    for r in rows:
        md = r["metadata"] or {}
        g[r["document_id"][:8]].append((r["seq"], md.get("epoch", "—")))
    return {k: sorted(v, key=lambda t: t[0]) for k, v in g.items()}


async def main() -> None:
    from sqlalchemy import text
    from app.core.db import SessionLocal
    from app.core.config import settings
    from app.services.rag.ingest import reingest_text
    from app.services.rag.milvus_client import ensure_collection

    async with SessionLocal() as session:
        docs = (await session.execute(text(
            "select id, kb_id, file_name, content, vector_epoch, chunk_count "
            "from documents where status='ready' and content is not null order by created_at"
        ))).all()

    if not docs:
        print("没有 status='ready' 且 content 非空的文档，无事可做")
        return

    print(f"模式：{'执行' if DO_IT else '演练（加 --yes 才真跑）'}")
    print("=" * 68)
    print(f"重建前：{len(snapshot())} 个文档在 Milvus 里有块")
    for did, v in snapshot().items():
        print(f"  {did}: {len(v)} 行  (seq, epoch)={v}")
    print(f"PG 侧：{len(docs)} 个 ready 文档待重灌")
    for did, kb, name, _c, ep, cc in docs:
        print(f"  {name:<24} epoch={ep} chunk_count={cc}")

    if not DO_IT:
        print("=" * 68)
        print("演练结束，未改动任何数据")
        return

    print("=" * 68)
    from pymilvus import MilvusClient
    from app.core.config import settings as st

    client = MilvusClient(uri=st.milvus_uri)
    if client.has_collection(st.milvus_collection):
        client.drop_collection(st.milvus_collection)
        print("已 drop collection")
    time.sleep(2)
    ensure_collection()
    client = MilvusClient(uri=st.milvus_uri)
    print(f"collection 已重建：{client.has_collection(st.milvus_collection)}")

    print("-" * 68)
    expect: dict[str, int] = {}
    for did, kb, name, content, ep, _cc in docs:
        did, kb, ep = str(did), str(kb), int(ep or 1)
        n = reingest_text(kb, did, content, {"file_name": name, "epoch": ep})
        expect[did[:8]] = ep
        async with SessionLocal() as session:
            await session.execute(
                text("update documents set chunk_count=:n where id=:i"), {"n": n, "i": did}
            )
            await session.commit()
        print(f"  {name:<24} 重灌 {n} 块  epoch={ep}")

    print("-" * 68)


    def verify(tag: str) -> bool:
        snap = snapshot()
        ok = True
        print(f"{tag}：{len(snap)} 个文档 / {sum(len(v) for v in snap.values())} 行")
        for did, v in snap.items():
            eps = {e for _, e in v}
            want = expect.get(did)
            good = len(v) == 1 and eps == {want}
            ok &= good
            print(f"  {did}: {len(v)} 行 epoch={sorted(eps, key=str)}  {'✅' if good else '❌ 期望 1 行 epoch=' + str(want)}")
        if set(snap.keys()) != set(expect.keys()):
            ok = False
            print(f"  ❌ 文档集合不一致：仅 Milvus {set(snap)-set(expect)}｜仅 PG {set(expect)-set(snap)}")
        return ok

    ok1 = verify("重建后立即校验")
    print(f"  等待 {RECHECK_DELAY}s 复校（捕捉延迟复活）…")
    time.sleep(RECHECK_DELAY)
    ok2 = verify("等待后复校")
    print("=" * 68)
    print(f">>> {'✅ 干净' if (ok1 and ok2) else '❌ 仍有残留，需继续排查'}")
    print("接着跑：./.venv/Scripts/python.exe playground/drift_check.py   （期望 drift=0 dup=0）")


asyncio.run(main())
