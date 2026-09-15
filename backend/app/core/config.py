"""全局配置（pydantic-settings，从环境变量 / .env 读取）。"""
from pydantic_settings import BaseSettings, SettingsConfigDict
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parents[2]

class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=BASE_DIR / ".env", extra="ignore")

    # 基础
    app_name: str = "知研 KnowPilot"
    version: str = "0.1.0"
    debug: bool = True
    quality_pass_score: int = 70

    # 数据库与中间件
    database_url: str = "postgresql+asyncpg://knowpilot:change-me@localhost/knowpilot"
    redis_url: str = "redis://localhost:6379/0"

    # Milvus
    milvus_uri: str = "http://localhost:19530"
    milvus_collection: str = "knowpilot_chunks"

    # JWT
    jwt_secret: str = "change-me"
    jwt_expire_minutes: int = 60 * 24 * 7

    # LLM（DeepSeek，模型分级）
    deepseek_api_key: str = ""
    deepseek_base_url: str = "https://api.deepseek.com"
    deepseek_planner_model: str = "deepseek:deepseek-v4-flash"
    deepseek_review_model: str = "deepseek:deepseek-v4-pro"

    # 嵌入模型
    dashscope_api_key: str = ""
    embedding_model: str = "qwen3.7-text-embedding"
    embedding_dimension: int = 1024
    openai_api_key: str = ""
    openai_embedding_model: str = "text-embedding-3-small"

    # 联网搜索（博查）
    bocha_api_key: str = ""


settings = Settings()
