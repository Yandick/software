from __future__ import annotations

import importlib
from pathlib import Path
from typing import Any

import pytest
from fastapi.testclient import TestClient


@pytest.fixture()
def client(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> TestClient:
    db_path = tmp_path / "test.db"
    monkeypatch.setenv("OPS_DATABASE_URL", f"sqlite:///{db_path}")
    monkeypatch.setenv("OPS_ENABLE_EMBEDDING_RAG", "false")

    from backend.app.config import get_settings

    get_settings.cache_clear()
    app_main = importlib.import_module("backend.app.main")
    from backend.app.services import issues_service
    from backend.app.services.llm_service import llm_service

    issues_service.UPLOAD_DIR = tmp_path / "uploads"
    issues_service.UPLOAD_DIR.mkdir(parents=True, exist_ok=True)

    from backend.app.database import init_db

    init_db()

    def fake_generate(question: str, context: str, enable_thinking: bool | None = None) -> dict[str, object]:
        return {
            "content": "测试回答：已检索知识库，建议先检查 VPN 证书和客户端版本；无法解决请转人工。",
            "ok": True,
            "reasoning_available": False,
            "reasoning_content": "",
            "reasoning_enabled": bool(enable_thinking),
            "status": "fake-vllm",
        }

    def fake_extract_issue_draft(description: str, rule_draft: dict[str, Any]) -> dict[str, Any]:
        return {
            **rule_draft,
            "attachment_url": "",
            "category": "network",
            "confidence": 0.91,
            "contact_phone": "13800138000",
            "extraction_source": "llm",
            "impact_scope": "远程办公",
            "llm_status": "fake-extract",
            "log_excerpt": "",
            "missing_fields": ["截图/附件链接", "错误日志或报错原文"],
            "priority": "medium",
            "title": "VPN 证书过期无法连接",
        }

    llm_service.generate = fake_generate
    llm_service.extract_issue_draft = fake_extract_issue_draft
    return TestClient(app_main.app)


def login(client: TestClient, username: str, password: str) -> dict[str, str]:
    response = client.post("/api/auth/login", json={"password": password, "username": username})
    assert response.status_code == 200, response.text
    return {"Authorization": f"Bearer {response.json()['access_token']}"}


@pytest.fixture()
def auth_headers(client: TestClient):
    def _login(username: str, password: str) -> dict[str, str]:
        return login(client, username, password)

    return _login
