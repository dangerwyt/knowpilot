"""Milvus 向量库客户端：collection 定义与建索引（对应 TDD 7.2）。

统一使用 pymilvus 的 MilvusClient API（高层封装，官方推荐新项目使用）。
pymilvus 采用惰性导入：Milvus 未安装/未就绪时，后端 API 仍可正常启动。
"""
import logging

from app.core.config import settings

logger = logging.getLogger(__name__)

DIM = 1024  # qwen3.7-text-embedding 向量维度


def _client():
    """构造 Milvus 客户端 —— **本模块唯一入口**（2026-09-16 抽出来）。

    抽它的理由：原先 6 处各自写 `MilvusClient(uri=settings.milvus_uri)`，
    加一个 token 参数就得改 6 行，换地址同理。

    token 留空是安全的：pymilvus 3.0.1 的 `MilvusClient.__init__` 签名里
    `token: str = ""` 本来就是默认值 ⇒ 传空串与不传是同一个调用。
    本地 Milvus 未开鉴权，实测连传一个错误的 token 也会被忽略。

    ⚠️ 每次调用都**新建**一个 client，与改造前 6 处各自构造的语义完全一致。
    不要加 @lru_cache 或模块级单例 —— 那会变成长期持有连接，是另一回事。
    """
    from pymilvus import MilvusClient

    return MilvusClient(uri=settings.milvus_uri, token=settings.milvus_token)


def ensure_collection(collection_name: str | None = None) -> None:
    """连接 Milvus 并确保 collection 存在（幂等）。启动时调用，容错记录告警。"""
    collection_name = collection_name or settings.milvus_collection
    try:
        from pymilvus import DataType, MilvusClient

        client = _client()
        if client.has_collection(collection_name):
            logger.info("Milvus collection %s 已存在", collection_name)
            return

        schema = MilvusClient.create_schema(auto_id=True)
        schema.add_field(field_name="id", datatype=DataType.INT64, is_primary=True, auto_id=True)
        schema.add_field(field_name="kb_id", datatype=DataType.VARCHAR, max_length=64)
        schema.add_field(field_name="document_id", datatype=DataType.VARCHAR, max_length=64)
        schema.add_field(field_name="seq", datatype=DataType.INT64)
        schema.add_field(field_name="content", datatype=DataType.VARCHAR, max_length=8192)
        schema.add_field(field_name="metadata", datatype=DataType.JSON)
        schema.add_field(field_name="embedding", datatype=DataType.FLOAT_VECTOR, dim=DIM)

        index_params = client.prepare_index_params()
        index_params.add_index(
            field_name="embedding",
            # AUTOINDEX —— 唯一「本地 + Zilliz Cloud 通吃」的选项。
            # Zilliz Cloud 自助只支持 AUTOINDEX（+ MINHASH_LSH）：IVF_FLAT / HNSW / IVF_PQ / DISKANN
            # 都要发工单申请 ⇒ 用 IVF_FLAT 生产建表必失败（2026-09-16 查证）。
            # 本地 Milvus v2.4.6 实测 AUTOINDEX + COSINE 建表/写入/检索/drop 全通 ⇒ 对本地零影响
            # （且本函数对已存在的 collection 直接 return，本地那份不会重建）。
            # ⚠️ AUTOINDEX 由服务端自动选索引，**不接受 params** —— 原先的 {"nlist": 1024} 必须删掉。
            index_type="AUTOINDEX",
            metric_type="COSINE",
        )

        client.create_collection(
            collection_name=collection_name,
            schema=schema,
            index_params=index_params,
        )
        logger.info("Milvus collection %s 已创建", collection_name)
    except Exception as exc:  # noqa: BLE001
        logger.warning("Milvus 初始化失败（服务可能未就绪），后续任务将重试: %s", exc)


def ingest_chunks(
        kb_id: str,
        document_id: str,
        chunks: list[str],
        embeddings: list[list[float]],
        metadata: dict | None = None,
) -> int:
    """将切块 + 向量写入 Milvus。返回写入条数。"""
    client = _client()
    rows = [
        {
            "kb_id": kb_id,
            "document_id": document_id,
            "seq": i,
            "content": chunk,
            "metadata": metadata or {},
            "embedding": embeddings[i],
        }
        for i, chunk in enumerate(chunks)
    ]
    client.insert(collection_name=settings.milvus_collection, data=rows)
    client.flush(collection_name=settings.milvus_collection)
    return len(client.query(
        collection_name=settings.milvus_collection,
        filter=f'document_id == "{document_id}"',
        output_fields=["document_id"],
    ))


def search(
        query_vec: list[float],
        kb_ids: list[str],
        top_k: int = 5,
        collection_name: str | None = None,
        document_ids: list[str] | None = None,
        epochs: dict[str, int] | None = None,
) -> list[dict]:
    """按 kb_id 检索；传了 document_ids 就再按文档白名单过滤。

    document_ids 三种取值：
        None   —— 未启用白名单（playground / 调试脚本），只按 kb_id 过滤
        []     —— 已启用但一条可检索文档都没有，直接返回 []
        [id...]—— 按白名单过滤
    """
    # 白名单为空 = 调用方明确告知"没有可检索文档"，直接返回，不碰 Milvus

    if epochs is not None:
        if not epochs:
            return []
    elif document_ids is not None and not document_ids:
        return []

    collection_name = collection_name or settings.milvus_collection
    client = _client()
    client.load_collection(collection_name)  # 检索前必须加载到内存

    if len(kb_ids) == 1:
        _kb = f'kb_id == "{kb_ids[0]}"'
    else:
        ids = ','.join(f'"{kb_id}"' for kb_id in kb_ids)
        _kb = f'kb_id in [{ids}]'

    if epochs is not None:
        _doc = " or ".join(
            f'((document_id == "{d}") and (metadata["epoch"] == {int(e)}))'
            for d, e in epochs.items()
        )
        _filter = f"({_kb}) and ({_doc})"
    elif document_ids is None:
        _filter = _kb
    else:
        _doc = 'document_id in [' + ','.join(f'"{d}"' for d in document_ids) + ']'
        _filter = f'({_kb}) and ({_doc})'

    res = client.search(
        collection_name=collection_name,
        data=[query_vec],
        limit=top_k,
        filter=_filter,
        output_fields=["content", "document_id"],
    )
    return [
        {
            "content": hit["entity"].get("content"),
            "document_id": hit["entity"].get("document_id"),
            "distance": hit["distance"]
        }
        for hit in res[0]
    ]


def delete_by_document(
        document_id: str,
        collection_name: str | None = None,
):
    collection_name = collection_name or settings.milvus_collection
    client = _client()
    _filter = f'document_id == "{document_id}"'
    left: list = []
    for attempt in range(2):
        client.delete(collection_name=collection_name, filter=_filter)
        client.flush(collection_name=collection_name)
        left = client.query(collection_name=collection_name, filter=_filter, output_fields=["document_id"])

        if not left:
            return
        if attempt == 0:
            logger.warning("删除后仍残留 %d 条记录，重试一次：%s", len(left), document_id)
    logger.warning("向量删除未生效，仍残留 %d 行，已忽略（脏块进不了检索白名单）：%s", len(left), document_id)


def list_chunks_by_document(
        document_id: str,
        collection_name: str | None = None
) -> list[dict]:
    collection_name = collection_name or settings.milvus_collection
    client = _client()
    rows = client.query(
        collection_name=collection_name,
        filter=f'document_id == "{document_id}"',
        output_fields=["seq", "content", "id"]
    )
    best: dict[int, dict] = {}
    for row in rows:
        cur = best.get(row["seq"])
        if cur is None or row["id"] > cur["id"]:
            best[row["seq"]] = row
    deduped = sorted(best.values(), key=lambda r: r["seq"])
    for r in deduped:
        r.pop("id", None)
    return deduped


def inspect_chunks_by_document(document_id: str, collection_name: str | None = None) -> tuple[int, list[int]]:
    """巡检用：返回 (真实行数, 重复的 seq 列表)。不做去重——重复必须可见。"""
    collection_name = collection_name or settings.milvus_collection
    client = _client()
    rows = client.query(
        collection_name=collection_name,
        filter=f'document_id == "{document_id}"',
        output_fields=["seq", "id"]
    )

    from collections import Counter
    counts = Counter(row["seq"] for row in rows)
    dups = [s for s, n in counts.items() if n > 1]
    return len(rows), sorted(dups)
