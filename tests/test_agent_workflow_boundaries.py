from __future__ import annotations

from typing import Callable

from fastapi.testclient import TestClient


def test_agent_status_cross_user_write_and_audit_boundaries(
    client: TestClient,
    auth_headers: Callable[[str, str], dict[str, str]],
) -> None:
    user_headers = auth_headers("user", "user123")
    ops_headers = auth_headers("ops", "ops123")
    auditor_headers = auth_headers("auditor", "audit123")

    status = client.get("/api/agent/status", headers=user_headers)
    assert status.status_code == 200, status.text
    status_body = status.json()
    assert status_body["mode"] == "single_qwen_multi_agent_orchestrator"
    assert {item["name"] for item in status_body["agents"]} >= {
        "supervisor",
        "risk_guardian",
        "ops_employee",
        "knowledge_curator",
        "evaluator",
    }
    assert all(item["prompt_loaded"] for item in status_body["agents"])
    assert {"knowledge_search", "issue_draft", "handoff_script", "knowledge_autonomous_ingest"} <= set(status_body["tools"])

    ask = client.post(
        "/api/qa/ask",
        headers=user_headers,
        json={"enable_thinking": False, "question": "VPN 无法连接，提示证书过期，影响远程办公，电话 13800138000"},
    )
    assert ask.status_code == 200, ask.text
    conversation_id = ask.json()["conversation_id"]

    cross_write = client.post(
        "/api/qa/ask",
        headers=ops_headers,
        json={"conversation_id": conversation_id, "question": "运维尝试继续普通用户会话"},
    )
    assert cross_write.status_code == 403, cross_write.text

    missing_handle = client.post("/api/issues/99999/handle", headers=ops_headers, json={"solution": "no such issue"})
    assert missing_handle.status_code == 404, missing_handle.text

    user_stats = client.get("/api/audit/stats", headers=user_headers)
    assert user_stats.status_code == 200, user_stats.text
    assert "accounts" not in user_stats.json()

    auditor_stats = client.get("/api/audit/stats", headers=auditor_headers)
    assert auditor_stats.status_code == 200, auditor_stats.text
    assert "accounts" in auditor_stats.json()

    audit_export = client.get("/api/audit/export", headers=auditor_headers)
    assert audit_export.status_code == 200, audit_export.text
    assert "log_type,id,event_type" in audit_export.json()["content"]


def test_knowledge_candidate_publish_and_sensitive_review_boundaries(
    client: TestClient,
    auth_headers: Callable[[str, str], dict[str, str]],
) -> None:
    ops_headers = auth_headers("ops", "ops123")
    admin_headers = auth_headers("admin", "admin123")

    ops_candidate = client.post(
        "/api/knowledge",
        headers=ops_headers,
        json={"content": "候选知识内容", "source_type": "faq", "status": "published", "tags": "pytest", "title": "ops 发布测试"},
    )
    assert ops_candidate.status_code == 200, ops_candidate.text
    assert ops_candidate.json()["status"] == "pending_review"

    publish_by_ops = client.post(
        f"/api/knowledge/{ops_candidate.json()['id']}/status",
        headers=ops_headers,
        json={"status": "published"},
    )
    assert publish_by_ops.status_code == 403, publish_by_ops.text

    ops_update_candidate = client.put(
        f"/api/knowledge/{ops_candidate.json()['id']}",
        headers=ops_headers,
        json={"content": "更新候选内容", "source_type": "faq", "status": "published", "tags": "pytest", "title": "ops 候选更新"},
    )
    assert ops_update_candidate.status_code == 200, ops_update_candidate.text
    assert ops_update_candidate.json()["status"] == "pending_review"

    sensitive_candidate = client.post(
        "/api/knowledge",
        headers=ops_headers,
        json={
            "content": "用户电话 13800138000，password=abc123",
            "source_type": "case",
            "status": "pending_review",
            "tags": "pytest",
            "title": "待脱敏候选",
        },
    )
    assert sensitive_candidate.status_code == 200, sensitive_candidate.text

    blocked_publish = client.post(
        f"/api/knowledge/{sensitive_candidate.json()['id']}/status",
        headers=admin_headers,
        json={"review_note": "包含敏感信息", "status": "published"},
    )
    assert blocked_publish.status_code == 400, blocked_publish.text
    assert blocked_publish.json()["detail"]["sensitive_check"]["blocking"] is True
