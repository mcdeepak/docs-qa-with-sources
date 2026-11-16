"""All configuration comes from environment variables. See .env.example."""
import os
from dataclasses import dataclass


def _get(name: str, default: str) -> str:
    return os.environ.get(name, default)


@dataclass(frozen=True)
class Settings:
    database_url: str = _get("DATABASE_URL", "postgresql://docsqa:docsqa@db:5432/docsqa")

    # Basic login. One shared account, as the brief asks for nothing more.
    app_username: str = _get("APP_USERNAME", "admin")
    app_password: str = _get("APP_PASSWORD", "admin")
    secret_key: str = _get("SECRET_KEY", "change-me-in-production")

    # Swappable parts. See app/retrieval and app/answering.
    retriever: str = _get("RETRIEVER", "bm25")
    answerer: str = _get("ANSWERER", "extractive")

    # Support decision. Set from eval results, see eval/README.md.
    support_threshold: float = float(_get("SUPPORT_THRESHOLD", "0.6"))
    top_k: int = int(_get("TOP_K", "5"))
    use_synonyms: bool = _get("USE_SYNONYMS", "true").lower() == "true"

    max_upload_mb: int = int(_get("MAX_UPLOAD_MB", "20"))

    # Worker.
    lease_seconds: int = int(_get("WORKER_LEASE_SECONDS", "20"))
    max_attempts: int = int(_get("WORKER_MAX_ATTEMPTS", "3"))
    poll_seconds: float = float(_get("WORKER_POLL_SECONDS", "0.5"))
    # Artificial per-stage delay, only used to demonstrate live status and crash recovery.
    index_delay_seconds: float = float(_get("INDEX_DELAY_SECONDS", "0"))

    # Optional LLM answerer (ANSWERER=llm). Any OpenAI-compatible endpoint.
    llm_base_url: str = _get("LLM_BASE_URL", "")
    llm_api_key: str = _get("LLM_API_KEY", "")
    llm_model: str = _get("LLM_MODEL", "")
    llm_timeout_seconds: float = float(_get("LLM_TIMEOUT_SECONDS", "8"))


settings = Settings()
