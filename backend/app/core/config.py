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
    # probe 资料相关性阈值（Milvus COSINE 相似度）
    # ⚠️ 单一阈值无解：正负例的 top1 相似度分布**重叠**（实测：有答案的样本最低 0.5333、
    # 无答案的最高 0.6739）⇒ 只能取折中，做不到零误判。
    #    0.41 偏松（12 条负例过线 8 条）；抬到 0.50 是零代价改善 —— 实测正例 top1 最低
    #    0.5333 ⇒ 假 False 恒为 0，负例过线数 8/12 → 4/12。
    #    但**仍未重新标定**，属已知短板。
    probe_min_score: float = 0.50

    # 数据库与中间件
    database_url: str = "postgresql+asyncpg://knowpilot:change-me@localhost/knowpilot"
    redis_url: str = "redis://localhost:6379/0"

    # Milvus —— 本地直连 或 Zilliz Cloud（托管）
    # 本地 Milvus 未开鉴权：token 留空即可（实测填个错 token 也能连上，会被忽略）
    # Zilliz Cloud 等托管实例：填控制台给的 "user:password" 或 API Key
    milvus_uri: str = "http://localhost:19530"
    milvus_collection: str = "knowpilot_chunks"
    milvus_token: str = ""

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
