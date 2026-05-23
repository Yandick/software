from __future__ import annotations

from typing import Callable

from fastapi.testclient import TestClient


def test_health_is_public(client: TestClient) -> None:
    response = client.get("/api/health")
    assert response.status_code == 200, response.text
    assert response.json()["status"] == "ok"


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
