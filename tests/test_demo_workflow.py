from __future__ import annotations

from typing import Callable

from fastapi.testclient import TestClient


def test_demo_closed_loop_is_visible_to_user(
    client: TestClient,
    auth_headers: Callable[[str, str], dict[str, str]],
) -> None:
    admin_headers = auth_headers("admin", "admin123")
    user_headers = auth_headers("user", "user123")

    demo = client.post("/api/demo/session", headers=admin_headers)
    assert demo.status_code == 200, demo.text
    demo_body = demo.json()
    demo_id = demo_body["id"]

    for _ in range(len(demo_body["steps"])):
        step = client.post(f"/api/demo/session/{demo_id}/step", headers=admin_headers)
        assert step.status_code == 200, step.text
        demo_body = step.json()

    assert demo_body["status"] == "finished"
    assert demo_body["ops_window"]["issue"]["status"] == "closed"
    assert demo_body["admin_window"]["knowledge"]["status"] == "published"
    assert demo_body["account_window"]["approval"]["status"] == "approved"
    assert demo_body["account_window"]["account"]["status"] == "active"
    assert demo_body["fallback_conversation_id"]

    issue_id = demo_body["ops_window"]["issue"]["id"]
    conversation_id = demo_body["conversation_id"]
    account_conversation_id = demo_body["account_conversation_id"]
    fallback_conversation_id = demo_body["fallback_conversation_id"]

    user_issues = client.get("/api/issues", headers=user_headers)
    assert user_issues.status_code == 200, user_issues.text
    assert any(item["id"] == issue_id for item in user_issues.json())

    user_conversations = client.get("/api/qa/conversations", headers=user_headers)
    assert user_conversations.status_code == 200, user_conversations.text
    assert any(item["id"] == conversation_id for item in user_conversations.json())
    assert any(item["id"] == account_conversation_id for item in user_conversations.json())
    assert any(item["id"] == fallback_conversation_id for item in user_conversations.json())


def test_non_admin_cannot_drive_demo(
    client: TestClient,
    auth_headers: Callable[[str, str], dict[str, str]],
) -> None:
    ops_headers = auth_headers("ops", "ops123")
    response = client.post("/api/demo/session", headers=ops_headers)
    assert response.status_code == 403, response.text
