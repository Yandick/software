from __future__ import annotations

import sqlite3
from pathlib import Path
from typing import Callable

import pytest
from fastapi.testclient import TestClient


def test_health_is_public(client: TestClient) -> None:
    response = client.get("/api/health")
    assert response.status_code == 200, response.text
    assert response.json()["status"] == "ok"
    assert response.json()["version"] == "1.0.0-demo"


def test_readiness_is_public_and_checks_backend_schema(client: TestClient) -> None:
    response = client.get("/api/ready")
    assert response.status_code == 200, response.text
    payload = response.json()
    assert payload["status"] == "ok"
    assert payload["version"] == "1.0.0-demo"
    checks = {item["name"]: item for item in payload["checks"]}
    assert checks["config"]["ok"] is True
    assert checks["database"]["ok"] is True


def test_readiness_reports_schema_drift(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    db_path = tmp_path / "legacy.db"
    with sqlite3.connect(db_path) as conn:
        conn.execute("create table users (id integer primary key, username text)")
        conn.execute("create table knowledge (id integer primary key, status text)")

    monkeypatch.setenv("OPS_DATABASE_URL", f"sqlite:///{db_path}")
    from backend.app.config import get_settings
    from backend.app.services.system_service import readiness

    get_settings.cache_clear()
    try:
        payload = readiness()
    finally:
        get_settings.cache_clear()

    checks = {item["name"]: item for item in payload["checks"]}
    detail = checks["database"]["detail"]
    assert payload["status"] == "error"
    assert checks["database"]["ok"] is False
    assert "issue_attachments" in detail["missing_tables"]
    assert "issue_events" in detail["missing_tables"]
    assert "password_hash" in detail["missing_columns"]["users"]
    assert "version" in detail["missing_columns"]["knowledge"]


def test_system_info_requires_login(
    client: TestClient,
    auth_headers: Callable[[str, str], dict[str, str]],
) -> None:
    unauthorized = client.get("/api/system/info")
    assert unauthorized.status_code == 401, unauthorized.text

    response = client.get("/api/system/info", headers=auth_headers("admin", "admin123"))
    assert response.status_code == 200, response.text
    payload = response.json()
    assert payload["app"] == "运维数字员工系统"
    assert payload["version"] == "1.0.0-demo"
    assert payload["database"]["engine"] == "sqlite"
    assert payload["features"]["rag"] is True
    assert "jwt_secret" not in payload


def test_status_routes_require_login(
    client: TestClient,
    auth_headers: Callable[[str, str], dict[str, str]],
) -> None:
    unauthorized = client.get("/api/llm/status")
    assert unauthorized.status_code == 401, unauthorized.text

    headers = auth_headers("admin", "admin123")
    llm = client.get("/api/llm/status", headers=headers)
    assert llm.status_code == 200, llm.text
    assert "ready" in llm.json()

    agent = client.get("/api/agent/status", headers=headers)
    assert agent.status_code == 200, agent.text
    assert agent.json()["mode"] == "controlled_react_prototype"
    assert set(agent.json()["tools"]) == {"handoff_script", "issue_draft", "knowledge_search"}
