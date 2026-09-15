from langchain_text_splitters import RecursiveCharacterTextSplitter
from app.services.rag import get_embedder
from app.services.rag.milvus_client import ingest_chunks, delete_by_document


def split_text(text: str, chunk_size: int = 220, chunk_overlap: int = 80) -> list[str]:
    splitter = RecursiveCharacterTextSplitter(
        chunk_size=chunk_size,
        chunk_overlap=chunk_overlap,
        separators=[
            '\n\n',
            '\n',
            '。',
            '；',
            '，',
            ' ',
            ''
        ]
    )
    return splitter.split_text(text)

def embed_text(text: str) -> tuple[list[str], list[list[float]]]:
    """切块 + 向量化。纯准备动作，失败时 Milvus 分毫未变。"""
    chunks = split_text(text)
    embedder = get_embedder()
    vectors = []
    batch = 20
    for i in range(0, len(chunks), batch):
        vectors.extend(embedder.embed_documents(chunks[i:i + batch]))
    return chunks, vectors

def reingest_text(kb_id: str, document_id: str, text: str, metadata: dict | None = None) -> int:
    """安全重灌：先算（可失败）→ 再删（破坏性）→ 后写（本地）。"""
    chunks, vectors = embed_text(text)
    delete_by_document(document_id)
    return ingest_chunks(kb_id, document_id, chunks, vectors, metadata)
