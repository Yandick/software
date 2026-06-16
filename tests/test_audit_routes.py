from __future__ import annotations

from typing import Callable

from fastapi.testclient import TestClient


def test_auditor_can_read_and_export_audit_logs(
    client: TestClient,
    auth_headers: Callable[[str, str], dict[str, str]],
) -> None:
    admin_headers = auth_headers("admin", "admin123")
    auditor_headers = auth_headers("auditor", "audit123")

    demo = client.post("/api/demo/session", headers=admin_headers)
    assert demo.status_code == 200, demo.text

    logs = client.get("/api/audit/logs", headers=auditor_headers)
    assert logs.status_code == 200, logs.text
    assert "audit" in logs.json()
    assert "qa" in logs.json()

    export = client.get("/api/audit/export", headers=auditor_headers)
    assert export.status_code == 200, export.text
    assert "log_type,id,event_type" in export.json()["content"]


def test_audit_export_sanitizes_csv_formula_prefix(
    client: TestClient,
    auth_headers: Callable[[str, str], dict[str, str]],
) -> None:
    from backend.app.database import audit

    audit("manual_test", "pytest", "\t=cmd")
    auditor_headers = auth_headers("auditor", "audit123")

    export = client.get("/api/audit/export", headers=auditor_headers, params={"q": "cmd"})

    assert export.status_code == 200, export.text
    assert "'\t=cmd" in export.json()["content"]


def test_audit_stats_keep_user_scope(
    client: TestClient,
    auth_headers: Callable[[str, str], dict[str, str]],
) -> None:
    user_headers = auth_headers("user", "user123")
    auditor_headers = auth_headers("auditor", "audit123")

    user_stats = client.get("/api/audit/stats", headers=user_headers)
    assert user_stats.status_code == 200, user_stats.text
    assert "accounts" not in user_stats.json()

    auditor_stats = client.get("/api/audit/stats", headers=auditor_headers)
    assert auditor_stats.status_code == 200, auditor_stats.text
    assert "accounts" in auditor_stats.json()


def test_ops_cannot_read_audit_logs(
    client: TestClient,
    auth_headers: Callable[[str, str], dict[str, str]],
) -> None:
    ops_headers = auth_headers("ops", "ops123")
    response = client.get("/api/audit/logs", headers=ops_headers)
    assert response.status_code == 403, response.text


def test_audit_limit_rejects_negative_values(
    client: TestClient,
    auth_headers: Callable[[str, str], dict[str, str]],
) -> None:
    auditor_headers = auth_headers("auditor", "audit123")

    logs = client.get("/api/audit/logs", headers=auditor_headers, params={"limit": -1})
    assert logs.status_code == 422, logs.text

    export = client.get("/api/audit/export", headers=auditor_headers, params={"limit": -1})
    assert export.status_code == 422, export.text
