from pydantic import ConfigDict, Field
from pydantic_settings import BaseSettings


class Settings(BaseSettings):
    """集中管理运行配置，统一从环境变量读取。"""

    model_config = ConfigDict(env_file=".env", env_prefix="CUTTER_")

    app_name: str = "semantic-document-cutter"
    debug: bool = False
    http_timeout_seconds: float = Field(default=20.0, gt=0)
    max_upload_mb: int = Field(default=20, ge=1)
    request_timeout_seconds: float = Field(default=30.0, gt=0)
    rate_limit_requests: int = Field(default=10, ge=1)
    rate_limit_window_seconds: int = Field(default=60, ge=1)

    # Chunk rules
    target_chunk_tokens: int = Field(default=300, ge=50)
    min_chunk_tokens: int = Field(default=100, ge=1)
    max_chunk_tokens: int = Field(default=450, ge=10)
    overlap_ratio: float = Field(default=0.0, ge=0.0, le=0.9)
    overlap_tokens: int = Field(default=0, ge=0)
    token_counter_provider: str = "heuristic"
    token_counter_endpoint: str | None = None
    token_counter_timeout_seconds: float = Field(default=10.0, gt=0)

    # Similarity
    similarity_enabled: bool = True
    similarity_high_threshold: float = Field(default=0.88, ge=-1.0, le=1.0)
    similarity_low_threshold: float = Field(default=0.72, ge=-1.0, le=1.0)
    embedding_base_url: str | None = None
    embedding_model: str = "DMetaSoul/Dmeta-embedding-zh"
    embedding_timeout_seconds: float = Field(default=10.0, gt=0)

    # Model switches
    llm_enabled: bool = False
    llm_provider: str = "openai"
    text_model: str | None = None
    vision_model: str | None = None
    flash_model: str | None = None
    openai_api_key: str | None = None
    openai_base_url: str | None = None
    vision_pdf_max_pages: int = Field(default=10, ge=1)
    pdf_ocr_fallback_min_chars: int = Field(default=40, ge=0)


settings = Settings()
