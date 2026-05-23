from __future__ import annotations

from pathlib import Path

import pytest

from backend.app.config import DEFAULT_JWT_SECRET, Settings, get_settings, validate_production_settings
from backend.app.database import connect, init_db


def test_production_rejects_default_jwt_secret() -> None:
    settings = Settings(environment="production", jwt_secret=DEFAULT_JWT_SECRET, seed_demo_accounts=False)

    with pytest.raises(RuntimeError, match="OPS_JWT_SECRET"):
        validate_production_settings(settings)


def test_production_rejects_demo_account_seed() -> None:
    settings = Settings(environment="production", jwt_secret="x" * 40, seed_demo_accounts=True)

    with pytest.raises(RuntimeError, match="OPS_SEED_DEMO_ACCOUNTS"):
        validate_production_settings(settings)


def test_production_accepts_secure_jwt_and_disabled_demo_seed() -> None:
    settings = Settings(environment="production", jwt_secret="x" * 40, seed_demo_accounts=False)

    validate_production_settings(settings)


def test_init_db_can_skip_demo_account_seed(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("OPS_DATABASE_URL", f"sqlite:///{tmp_path / 'no_seed.db'}")
    monkeypatch.setenv("OPS_SEED_DEMO_ACCOUNTS", "false")
    get_settings.cache_clear()

    try:
        init_db()
        with connect() as conn:
            user_count = conn.execute("select count(*) from users").fetchone()[0]
            knowledge_count = conn.execute("select count(*) from knowledge").fetchone()[0]
    finally:
        get_settings.cache_clear()

    assert user_count == 0
    assert knowledge_count > 0
