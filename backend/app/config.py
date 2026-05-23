from functools import lru_cache
from pathlib import Path
from pydantic_settings import BaseSettings, SettingsConfigDict

DEFAULT_JWT_SECRET = "please-change-this-secret-for-dev-only-32"


class Settings(BaseSettings):
    app_name: str = "运维数字员工系统"
    api_prefix: str = "/api"
    database_url: str = "sqlite:///backend/data/app.db"
    environment: str = "development"
    jwt_secret: str = DEFAULT_JWT_SECRET
    jwt_algorithm: str = "HS256"
    access_token_expire_minutes: int = 60 * 8
    seed_demo_accounts: bool = True
    model_path: str = "models/qwen3-1.7b"
    vllm_base_url: str = "http://127.0.0.1:8000/v1"
    vllm_model_name: str = "qwen3-1.7b"
    enable_thinking: bool = False
    max_new_tokens: int = 512
    temperature: float = 0.6
    top_p: float = 0.95

    model_config = SettingsConfigDict(env_file=".env", env_prefix="OPS_", extra="ignore")

    @property
    def db_path(self) -> Path:
        if self.database_url.startswith("sqlite:///"):
            return Path(self.database_url.removeprefix("sqlite:///"))
        return Path("backend/data/app.db")

    @property
    def is_production(self) -> bool:
        return self.environment.lower() in {"prod", "production"}


def validate_production_settings(settings: Settings) -> None:
    if not settings.is_production:
        return
    errors = []
    if settings.jwt_secret == DEFAULT_JWT_SECRET or len(settings.jwt_secret) < 32:
        errors.append("OPS_JWT_SECRET must be set to a non-default value with at least 32 characters")
    if settings.seed_demo_accounts:
        errors.append("OPS_SEED_DEMO_ACCOUNTS must be false in production")
    if errors:
        raise RuntimeError("Unsafe production configuration: " + "; ".join(errors))


@lru_cache
def get_settings() -> Settings:
    return Settings()
