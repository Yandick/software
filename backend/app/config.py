from functools import lru_cache
from pathlib import Path
from pydantic_settings import BaseSettings, SettingsConfigDict

DEFAULT_JWT_SECRET = "please-change-this-secret-for-dev-only-32"
KNOWN_ENVIRONMENTS = {"dev", "development", "prod", "production", "staging", "test", "testing"}
PRODUCTION_ENVIRONMENTS = {"prod", "production", "staging"}


class Settings(BaseSettings):
    app_name: str = "运维数字员工系统"
    app_version: str = "1.0.0"
    api_prefix: str = "/api"
    database_url: str = "sqlite:///backend/data/app.db"
    environment: str = "development"
    jwt_secret: str = DEFAULT_JWT_SECRET
    jwt_algorithm: str = "HS256"
    access_token_expire_minutes: int = 60 * 8
    seed_demo_accounts: bool = True
    cors_origins: str = "http://127.0.0.1:5666,http://localhost:5666"
    model_path: str = "models/qwen3-1.7b"
    vllm_base_url: str = "http://127.0.0.1:8000/v1"
    vllm_model_name: str = "qwen3-1.7b"
    enable_thinking: bool = False
    enable_agent_llm: bool = True
    agent_llm_parallelism: int = 5
    agent_llm_timeout_seconds: int = 45
    enable_intent_router_llm: bool = False
    intent_router_llm_min_confidence: float = 0.72
    enable_embedding_rag: bool = True
    embedding_model_path: str = "models/qwen3-embedding-0.6b"
    embedding_device: str = "auto"
    embedding_batch_size: int = 8
    embedding_max_length: int = 8192
    embedding_dimension: int = 1024
    embedding_index_dir: str = "backend/data/vector_index"
    embedding_index_backend: str = "auto"
    embedding_query_instruction: str = (
        "Given an enterprise IT operations support query, retrieve relevant private knowledge base passages that answer the query"
    )
    max_new_tokens: int = 512
    temperature: float = 0.6
    top_p: float = 0.95

    model_config = SettingsConfigDict(env_file=".env", env_prefix="OPS_", extra="ignore")

    @property
    def db_path(self) -> Path:
        if self.database_url.startswith("sqlite:///"):
            return Path(self.database_url.removeprefix("sqlite:///"))
        raise ValueError("Only sqlite:/// OPS_DATABASE_URL is supported by the runtime database layer")

    @property
    def environment_name(self) -> str:
        return self.environment.strip().lower()

    @property
    def is_production(self) -> bool:
        return self.environment_name in PRODUCTION_ENVIRONMENTS

    @property
    def cors_origin_list(self) -> list[str]:
        values = [item.strip() for item in self.cors_origins.split(",") if item.strip()]
        return values or ["http://127.0.0.1:5666", "http://localhost:5666"]


def validate_production_settings(settings: Settings) -> None:
    if settings.environment_name not in KNOWN_ENVIRONMENTS:
        raise RuntimeError(
            "Unsafe environment configuration: OPS_ENVIRONMENT must be one of "
            + ", ".join(sorted(KNOWN_ENVIRONMENTS))
        )
    if not settings.is_production:
        return
    errors = []
    if settings.jwt_secret == DEFAULT_JWT_SECRET or len(settings.jwt_secret) < 32:
        errors.append("OPS_JWT_SECRET must be set to a non-default value with at least 32 characters")
    if settings.seed_demo_accounts:
        errors.append("OPS_SEED_DEMO_ACCOUNTS must be false in production")
    if "*" in settings.cors_origin_list:
        errors.append("OPS_CORS_ORIGINS must not contain * in production")
    if errors:
        raise RuntimeError("Unsafe production configuration: " + "; ".join(errors))


@lru_cache
def get_settings() -> Settings:
    return Settings()
