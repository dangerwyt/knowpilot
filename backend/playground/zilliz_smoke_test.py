"""向量库上云预检 —— 对**当前配置指向的** Milvus / Zilliz Cloud 跑一遍全链路。

跑什么（都走生产代码本体，不另写一套实现）：
    ensure_collection → ingest_chunks → search → list_chunks_by_document → delete_by_document

为什么需要它：`ensure_collection()` 的 `try/except` **只 logger.warning**，索引类型不被服务端接受时
服务照常启动、一上传文档才报错 —— 极易误判成网络问题。用这个脚本可以在"部署完成"和"第一次上传"
之间插一个 30 秒的判据，把「向量库没配对」和「应用有问题」当场分开。

用法（在仓库里跑，会自动读 backend/.env）：
    backend/.venv/Scripts/python.exe backend/playground/zilliz_smoke_test.py

临时指向别的集群（不改 .env，pydantic 的 BaseSettings 天然认环境变量）：
    MILVUS_URI=https://in03-xxx.serverless.aws-eu-central-1.cloud.zilliz.com \
    MILVUS_TOKEN=xxx \
    backend/.venv/Scripts/python.exe backend/playground/zilliz_smoke_test.py

⚠️ 只在**独立临时集合** `_kp_smoke_<日期>` 上操作，finally 里一定 drop。
退出码 0 = 全过，1 = 有 FAIL。

────────────────────────────────────────────────────────────────────
⚠️ 为什么要把 settings.milvus_collection 临时改掉（第一版在这里栽过）：
   `milvus_client.ingest_chunks()` 把目标集合**写死**读 `settings.milvus_collection`，
   它没有 `collection_name` 参数（另外几个函数都有）。所以想让它写进临时集合，
   只能临时改配置。第一版没改，于是 `ensure_collection()` 老实地去建了**正式的
   knowpilot_chunks**，而脚本的断言看的是临时名 —— 断言 FAIL、真集合却被建了出来。
   两条名字不一致 ⇒ 判据和动作各说各话，什么结论都得不出来。
   现在补两道保险：① 改配置；② 断言「目标名必须以 _kp_smoke_ 开头」才允许继续。
────────────────────────────────────────────────────────────────────
"""
import sys
from datetime import date
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from pymilvus import MilvusClient  # noqa: E402

from app.core.config import settings  # noqa: E402
from app.services.rag import milvus_client as mc  # noqa: E402

TMP_PREFIX = "_kp_smoke_"
COLL = f"{TMP_PREFIX}{date.today():%Y%m%d}"
DIM = mc.DIM

ok: list[str] = []
bad: list[str] = []


def step(name: str, cond: bool, detail: str = "") -> None:
    (ok if cond else bad).append(name)
    print(f"  [{'PASS' if cond else 'FAIL'}] {name}" + (f" —— {detail}" if detail else ""))


def main() -> int:
    configured = settings.milvus_collection      # 显示用：这是正式集合名，本脚本绝不在它上面写

    print(f"目标 URI          = {settings.milvus_uri}")
    print(f"配置里的正式集合   = {configured}（本脚本不碰它）")
    print(f"本次操作临时集合   = {COLL}（跑完即 drop）")

    if "zilliz" in settings.milvus_uri.lower() and not settings.milvus_token:
        print("\n⚠️ URI 指向 Zilliz 但 MILVUS_TOKEN 为空 —— 后面必然鉴权失败，先补 token。")

    # 硬保险：把操作目标切到临时集合，且切完必须真的是临时名
    settings.milvus_collection = COLL
    if not settings.milvus_collection.startswith(TMP_PREFIX):
        sys.exit(f"拒绝运行：操作目标 {settings.milvus_collection!r} 不是临时集合")

    try:
        client = MilvusClient(uri=settings.milvus_uri, token=settings.milvus_token)
    except Exception as exc:  # noqa: BLE001
        # 最常见的失败就是这个：本地 Docker 没起，或服务器上 Milvus 地址/端口没通。
        # 裸 traceback 会让人以为是脚本坏了 —— 这里直接说清"是连不上"。
        # ⚠️ 必须记一条 FAIL：早先只 print 就 return，结果是「0 PASS / 0 FAIL + 退出码 0」
        # —— 一条断言都没跑却报成功，是最典型的假绿。
        print(f"\n❌ 连不上向量库：{settings.milvus_uri}")
        print(f"   {type(exc).__name__}: {exc}")
        print("   排查顺序：① 本地跑的话 Docker 里 Milvus 起了吗"
              "（docker ps | grep milvus）；")
        print("            ② 上云的话 URI/token 对不对、服务器安全组/出网通不通；")
        print("            ③ Zilliz 控制台的集群是不是 paused。")
        step("连上向量库", False, f"{settings.milvus_uri} 不可达")
        return _summary()

    try:
        print(f"server_version    = {client.get_server_version()}")
    except Exception as exc:  # noqa: BLE001
        print(f"server_version    = 取不到（{type(exc).__name__}: {exc}）")

    if client.has_collection(COLL):
        client.drop_collection(COLL)   # 上次跑挂了留下的
        print(f"（清掉上次残留的 {COLL}）")

    try:
        print(f"\n[1] ensure_collection() 建表 —— 目标 {settings.milvus_collection}")
        mc.ensure_collection()
        created = client.has_collection(COLL)
        step("集合已创建", created,
             "" if created else "建表失败 —— 回看后端日志里那句 'Milvus 初始化失败' 的 warning")

        if not created:
            return _summary()

        try:
            idxs = client.list_indexes(COLL)
            print(f"      索引 = {idxs}")
            for i in idxs:
                info = client.describe_index(COLL, i)
                print(f"      {i}: type={info.get('index_type')} metric={info.get('metric_type')}")
                # Zilliz Cloud 自助只支持 AUTOINDEX（+MINHASH_LSH）；本地 Milvus 用 AUTOINDEX 也通
                # ⇒ 这条必须成立，否则上云必挂（且只 warning，不拦启动）。
                step("索引类型 = AUTOINDEX", info.get("index_type") == "AUTOINDEX",
                     f"实际={info.get('index_type')}")
                step("度量 = COSINE", info.get("metric_type") == "COSINE",
                     f"实际={info.get('metric_type')}")
        except Exception as exc:  # noqa: BLE001
            step("读到索引信息", False, f"{type(exc).__name__}: {exc}")

        print(f"\n[2] ingest_chunks() 写入 2 条（正交单位向量，{DIM} 维）")
        v0 = [0.0] * DIM
        v0[0] = 1.0
        v1 = [0.0] * DIM
        v1[1] = 1.0
        n = mc.ingest_chunks(
            kb_id="smoke_kb",
            document_id="smoke_doc",
            chunks=["甲：第一条测试内容", "乙：第二条测试内容"],
            embeddings=[v0, v1],
            metadata={"epoch": 1},
        )
        step("写入返回条数 = 2", n == 2, f"实际={n}")

        print("\n[3] search() 用 v0 检索（期望 top1 = 甲，相似度≈1.0）")
        hits = mc.search(query_vec=v0, kb_ids=["smoke_kb"], top_k=2)
        for h in hits:
            print(f"      distance={h['distance']:.4f}  content={h['content'][:20]}")
        step("检索返回 2 条", len(hits) == 2, f"实际={len(hits)}")
        if hits:
            step("top1 = 甲", hits[0]["content"].startswith("甲"), f"实际={hits[0]['content'][:6]}")
            # 方向判据：Milvus 在 COSINE 下 distance 装的是**相似度**（越大越像）。
            # 哪天变成"越小越像"，这条会红 —— 生产里所有阈值都得跟着反过来。
            step("top1 相似度 > 0.99（越大越像）", hits[0]["distance"] > 0.99,
                 f"实际={hits[0]['distance']:.4f}")

        print("\n[4] list_chunks_by_document() 按 seq 有序")
        rows = mc.list_chunks_by_document("smoke_doc")
        seqs = [r["seq"] for r in rows]
        step("拿到 2 块且 seq=[0,1]", seqs == [0, 1], f"实际={seqs}")

        print("\n[5] delete_by_document() 删除 + 复检")
        mc.delete_by_document("smoke_doc")
        left = mc.list_chunks_by_document("smoke_doc")
        step("删干净（残留 0）", len(left) == 0, f"实际残留={len(left)}")

    finally:
        print("\n[6] 清理临时集合")
        try:
            client.drop_collection(COLL)
            step("临时集合已 drop", not client.has_collection(COLL))
        except Exception as exc:  # noqa: BLE001
            step("临时集合已 drop", False, f"{type(exc).__name__}: {exc}")
        # 收尾复核：确认没在集群上留下任何东西（包括正式集合）
        try:
            rest = client.list_collections()
            print(f"      集群剩余 collections = {rest if rest else '（空）'}")
            if rest:
                print("      ⚠️ 上面若有非 _kp_smoke_ 开头的集合，确认一下是不是本该存在的。")
        except Exception as exc:  # noqa: BLE001
            print(f"      list_collections 失败：{type(exc).__name__}: {exc}")
        settings.milvus_collection = configured

    return _summary()


def _summary() -> int:
    print("\n" + "=" * 60)
    print(f"结果：{len(ok)} PASS / {len(bad)} FAIL")
    if bad:
        print("失败项：" + "、".join(bad))
    return 1 if bad else 0


if __name__ == "__main__":
    sys.exit(main())
