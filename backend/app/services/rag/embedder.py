from langchain_community.embeddings import DashScopeEmbeddings
from app.core.config import settings

_embedder = None


def get_embedder():
    global _embedder
    if _embedder is None:
        _embedder = DashScopeEmbeddings(
            model=settings.embedding_model,
            dashscope_api_key=settings.dashscope_api_key
        )
    return _embedder
