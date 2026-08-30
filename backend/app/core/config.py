"""Typed application settings. Every value is env-overridable; see .env.example."""

from __future__ import annotations

from functools import lru_cache
from typing import Literal

from pydantic import Field, computed_field
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env", env_file_encoding="utf-8", extra="ignore", case_sensitive=False
    )

    # ---- app ----
    env: Literal["development", "production"] = Field("development", alias="NYAYA_ENV")
    log_level: str = Field("INFO", alias="NYAYA_LOG_LEVEL")
    api_port: int = Field(8000, alias="NYAYA_API_PORT")
    session_secret: str = Field("dev-only-insecure-secret", alias="NYAYA_SESSION_SECRET")

    # ---- postgres ----
    postgres_user: str = Field("nyaya", alias="POSTGRES_USER")
    postgres_password: str = Field("nyaya", alias="POSTGRES_PASSWORD")
    postgres_db: str = Field("nyaya", alias="POSTGRES_DB")
    postgres_host: str = Field("postgres", alias="POSTGRES_HOST")
    postgres_port: int = Field(5432, alias="POSTGRES_PORT")
    # Render and most managed providers hand you a single URL; it wins when present.
    database_url_override: str | None = Field(None, alias="DATABASE_URL")

    # ---- redis ----
    redis_host: str = Field("redis", alias="REDIS_HOST")
    redis_port: int = Field(6379, alias="REDIS_PORT")
    redis_url_override: str | None = Field(None, alias="REDIS_URL")

    # ---- vector store ----
    vector_store: Literal["qdrant", "pgvector"] = Field("qdrant", alias="NYAYA_VECTOR_STORE")
    qdrant_url: str = Field("http://qdrant:6333", alias="QDRANT_URL")
    qdrant_collection_statute: str = Field(
        "nyaya_statute", alias="QDRANT_COLLECTION_STATUTE"
    )
    qdrant_collection_docs: str = Field(
        "nyaya_documents", alias="QDRANT_COLLECTION_DOCS"
    )

    # ---- embeddings / rerank ----
    embed_model: str = Field("BAAI/bge-small-en-v1.5", alias="NYAYA_EMBED_MODEL")
    embed_dim: int = Field(384, alias="NYAYA_EMBED_DIM")
    embed_query_prefix: str = Field("", alias="NYAYA_EMBED_QUERY_PREFIX")
    embed_passage_prefix: str = Field("", alias="NYAYA_EMBED_PASSAGE_PREFIX")
    rerank_model: str = Field("Xenova/ms-marco-MiniLM-L-6-v2", alias="NYAYA_RERANK_MODEL")
    rerank_enabled: bool = Field(True, alias="NYAYA_RERANK_ENABLED")
    rerank_top_k: int = Field(30, alias="NYAYA_RERANK_TOP_K")
    rerank_keep: int = Field(6, alias="NYAYA_RERANK_KEEP")

    # ---- retrieval thresholds ----
    confidence_high: float = Field(0.55, alias="NYAYA_CONFIDENCE_HIGH")
    confidence_low: float = Field(0.30, alias="NYAYA_CONFIDENCE_LOW")
    rrf_k: int = Field(60, alias="NYAYA_RRF_K")
    hybrid_candidates: int = Field(50, alias="NYAYA_HYBRID_CANDIDATES")

    # ---- llm ----
    llm_provider: Literal["groq", "ollama", "openrouter", "gemini"] = Field(
        "groq", alias="NYAYA_LLM_PROVIDER"
    )
    llm_model: str = Field("llama-3.3-70b-versatile", alias="NYAYA_LLM_MODEL")
    llm_api_key: str = Field("", alias="NYAYA_LLM_API_KEY")
    llm_base_url: str = Field("", alias="NYAYA_LLM_BASE_URL")
    llm_timeout_s: int = Field(60, alias="NYAYA_LLM_TIMEOUT_S")
    llm_max_tokens: int = Field(1024, alias="NYAYA_LLM_MAX_TOKENS")
    llm_temperature: float = Field(0.1, alias="NYAYA_LLM_TEMPERATURE")
    ollama_base_url: str = Field("http://host.docker.internal:11434", alias="OLLAMA_BASE_URL")
    ollama_model: str = Field("qwen2.5:7b-instruct", alias="OLLAMA_MODEL")

    # ---- uploads ----
    max_upload_mb: int = Field(25, alias="NYAYA_MAX_UPLOAD_MB")
    allowed_mime: str = Field(
        "application/pdf,image/png,image/jpeg,text/plain", alias="NYAYA_ALLOWED_MIME"
    )
    upload_dir: str = Field("/data/uploads", alias="NYAYA_UPLOAD_DIR")

    # ---- rate limits ----
    rate_limit_chat: str = Field("30/minute", alias="NYAYA_RATE_LIMIT_CHAT")
    rate_limit_upload: str = Field("10/hour", alias="NYAYA_RATE_LIMIT_UPLOAD")

    # ---- ingestion ----
    source_pdf: str = Field("/data/raw/bnss_2023.pdf", alias="NYAYA_SOURCE_PDF")
    forms_page_start: int = Field(190, alias="NYAYA_FORMS_PAGE_START")
    forms_page_end: int = Field(249, alias="NYAYA_FORMS_PAGE_END")
    ocr_enabled: bool = Field(True, alias="NYAYA_OCR_ENABLED")
    ocr_lang: str = Field("eng", alias="NYAYA_OCR_LANG")

    # ---- cost accounting ----
    cost_per_1m_input_usd: float = Field(0.59, alias="NYAYA_COST_PER_1M_INPUT_USD")
    cost_per_1m_output_usd: float = Field(0.79, alias="NYAYA_COST_PER_1M_OUTPUT_USD")

    # ---------------------------------------------------------------- derived
    @computed_field  # type: ignore[prop-decorator]
    @property
    def database_url(self) -> str:
        """Async SQLAlchemy DSN. A provider-supplied DATABASE_URL wins."""
        if self.database_url_override:
            url = self.database_url_override
            # Render/Heroku hand out postgres:// or postgresql://; we need asyncpg.
            for prefix in ("postgresql+asyncpg://", "postgresql://", "postgres://"):
                if url.startswith(prefix):
                    return "postgresql+asyncpg://" + url[len(prefix) :]
            return url
        return (
            f"postgresql+asyncpg://{self.postgres_user}:{self.postgres_password}"
            f"@{self.postgres_host}:{self.postgres_port}/{self.postgres_db}"
        )

    @computed_field  # type: ignore[prop-decorator]
    @property
    def redis_url(self) -> str:
        if self.redis_url_override:
            return self.redis_url_override
        return f"redis://{self.redis_host}:{self.redis_port}"

    @computed_field  # type: ignore[prop-decorator]
    @property
    def allowed_mime_set(self) -> frozenset[str]:
        return frozenset(m.strip() for m in self.allowed_mime.split(",") if m.strip())

    @computed_field  # type: ignore[prop-decorator]
    @property
    def max_upload_bytes(self) -> int:
        return self.max_upload_mb * 1024 * 1024


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    return Settings()  # type: ignore[call-arg]


settings = get_settings()
