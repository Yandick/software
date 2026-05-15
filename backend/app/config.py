from functools import lru_cache
from pathlib import Path
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    app_name: str = "运维数字员工系统"
    api_prefix: str = "/api"
    database_url: str = "sqlite:///backend/data/app.db"
    jwt_secret: str = "please-change-this-secret-for-dev-only-32"
    jwt_algorithm: str = "HS256"
    access_token_expire_minutes: int = 60 * 8
    model_path: str = "models/qwen3-1.7b"
    inference_backend: str = "auto"
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


@lru_cache
def get_settings() -> Settings:
    return Settings()
