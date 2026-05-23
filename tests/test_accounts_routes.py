from __future__ import annotations

from typing import Callable

from fastapi.testclient import TestClient


def create_account(client: TestClient, headers: dict[str, str], name: str) -> int:
    response = client.post(
        "/api/accounts",
        headers=headers,
        json={
            "account_name": name,
            "contact_phone": "010-12345678",
            "department": "运维中心",
            "expires_at": "2099-12-31",
            "owner_name": "测试负责人",
            "permission_scope": "basic_ops",
            "remark": "pytest",
            "risk_level": "medium",
        },
    )
    assert response.status_code == 200, response.text
    return int(response.json()["id"])


def test_admin_can_create_list_and_export_accounts(
    client: TestClient,
    auth_headers: Callable[[str, str], dict[str, str]],
) -> None:
    admin_headers = auth_headers("admin", "admin123")
    account_id = create_account(client, admin_headers, "pytest_ops_account")

    listing = client.get("/api/accounts", params={"q": "pytest_ops_account"}, headers=admin_headers)
    assert listing.status_code == 200, listing.text
    rows = listing.json()
    assert rows[0]["id"] == account_id
    assert rows[0]["expiry_status"] == "valid"

    export = client.get("/api/accounts/export", params={"q": "pytest_ops_account"}, headers=admin_headers)
    assert export.status_code == 200, export.text
    assert export.json()["count"] == 1
    assert "账号名" in export.json()["content"]


def test_user_cannot_access_account_console(
    client: TestClient,
    auth_headers: Callable[[str, str], dict[str, str]],
) -> None:
    user_headers = auth_headers("user", "user123")
    response = client.get("/api/accounts", headers=user_headers)
    assert response.status_code == 403, response.text


def test_account_update_approval_flow(
    client: TestClient,
    auth_headers: Callable[[str, str], dict[str, str]],
) -> None:
    admin_headers = auth_headers("admin", "admin123")
    ops_headers = auth_headers("ops", "ops123")
    account_id = create_account(client, admin_headers, "pytest_update_account")

    update = client.put(
        f"/api/accounts/{account_id}",
        headers=ops_headers,
        json={"owner_name": "新负责人", "risk_level": "high"},
    )
    assert update.status_code == 200, update.text
    approval_id = update.json()["approval_id"]

    pending = client.get("/api/account-approvals", params={"status": "pending"}, headers=admin_headers)
    assert pending.status_code == 200, pending.text
    assert any(item["id"] == approval_id for item in pending.json())

    decision = client.post(
        f"/api/account-approvals/{approval_id}/decision",
        headers=admin_headers,
        json={"decision": "approved", "reason": "pytest approve"},
    )
    assert decision.status_code == 200, decision.text
    assert decision.json()["status"] == "approved"

    listing = client.get("/api/accounts", params={"q": "pytest_update_account"}, headers=admin_headers)
    assert listing.status_code == 200, listing.text
    row = listing.json()[0]
    assert row["owner_name"] == "新负责人"
    assert row["risk_level"] == "high"


def test_account_freeze_and_unfreeze_approval_flow(
    client: TestClient,
    auth_headers: Callable[[str, str], dict[str, str]],
) -> None:
    admin_headers = auth_headers("admin", "admin123")
    account_id = create_account(client, admin_headers, "pytest_freeze_account")

    freeze = client.post(f"/api/accounts/{account_id}/freeze", headers=admin_headers)
    assert freeze.status_code == 200, freeze.text
    freeze_decision = client.post(
        f"/api/account-approvals/{freeze.json()['approval_id']}/decision",
        headers=admin_headers,
        json={"decision": "approved", "reason": "pytest freeze"},
    )
    assert freeze_decision.status_code == 200, freeze_decision.text
    frozen = client.get("/api/accounts", params={"q": "pytest_freeze_account"}, headers=admin_headers)
    assert frozen.json()[0]["status"] == "frozen"

    unfreeze = client.post(f"/api/accounts/{account_id}/unfreeze", headers=admin_headers)
    assert unfreeze.status_code == 200, unfreeze.text
    unfreeze_decision = client.post(
        f"/api/account-approvals/{unfreeze.json()['approval_id']}/decision",
        headers=admin_headers,
        json={"decision": "approved", "reason": "pytest unfreeze"},
    )
    assert unfreeze_decision.status_code == 200, unfreeze_decision.text
    active = client.get("/api/accounts", params={"q": "pytest_freeze_account"}, headers=admin_headers)
    assert active.json()[0]["status"] == "active"
