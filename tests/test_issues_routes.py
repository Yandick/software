from __future__ import annotations

from typing import Callable

from fastapi.testclient import TestClient


def issue_payload(title: str, attachment_url: str = "") -> dict[str, str]:
    return {
        "attachment_url": attachment_url,
        "category": "network",
        "contact_phone": "13800138000",
        "description": "VPN 提示证书过期，远程办公无法连接。",
        "impact_scope": "远程办公",
        "log_excerpt": "error certificate expired",
        "priority": "medium",
        "title": title,
    }


def create_issue(client: TestClient, headers: dict[str, str], title: str, attachment_url: str = "") -> int:
    response = client.post("/api/issues", headers=headers, json=issue_payload(title, attachment_url))
    assert response.status_code == 200, response.text
    assert response.json()["status"] == "submitted"
    return int(response.json()["id"])


def add_extra_user(username: str = "pytest_user2") -> None:
    from backend.app.database import _hash_password, connect, utc_now

    with connect() as conn:
        conn.execute(
            "insert into users(username,password_hash,real_name,role,department,created_at) values(?,?,?,?,?,?)",
            (username, _hash_password("user2123"), "普通用户二", "user", "业务部门", utc_now()),
        )


def test_issue_draft_create_handle_visit_and_feedback(
    client: TestClient,
    auth_headers: Callable[[str, str], dict[str, str]],
) -> None:
    user_headers = auth_headers("user", "user123")
    ops_headers = auth_headers("ops", "ops123")

    draft = client.post("/api/issues/draft", headers=user_headers, json={"description": "VPN 无法连接，电话 13800138000"})
    assert draft.status_code == 200, draft.text
    assert draft.json()["extraction_source"] == "llm"

    issue_id = create_issue(client, user_headers, "pytest_issue_flow")

    listing = client.get("/api/issues", headers=user_headers, params={"q": "pytest_issue_flow"})
    assert listing.status_code == 200, listing.text
    row = listing.json()[0]
    assert row["id"] == issue_id
    assert row["status_label"] == "已提交"
    assert row["events"][0]["event_type"] == "created"

    accept = client.post(f"/api/issues/{issue_id}/accept", headers=ops_headers)
    assert accept.status_code == 200, accept.text
    assert accept.json()["status"] == "accepted"

    status = client.post(
        f"/api/issues/{issue_id}/status",
        headers=ops_headers,
        json={"note": "pytest processing", "status": "processing"},
    )
    assert status.status_code == 200, status.text
    assert status.json()["status_label"] == "处理中"

    assist = client.get(f"/api/issues/{issue_id}/assist", headers=ops_headers)
    assert assist.status_code == 200, assist.text
    assert "suggested_steps" in assist.json()
    assert "knowledge_candidate" in assist.json()

    handle = client.post(f"/api/issues/{issue_id}/handle", headers=ops_headers, json={"solution": "刷新 VPN 证书后恢复"})
    assert handle.status_code == 200, handle.text
    assert handle.json()["status"] == "pending_visit"

    visit = client.post(
        f"/api/issues/{issue_id}/visit",
        headers=ops_headers,
        json={"resolved": True, "satisfaction_score": 5, "visit_result": "用户确认恢复"},
    )
    assert visit.status_code == 200, visit.text
    assert visit.json()["status"] == "closed"

    feedback = client.post(
        f"/api/issues/{issue_id}/feedback",
        headers=user_headers,
        json={"feedback": "处理及时", "satisfaction_score": 5},
    )
    assert feedback.status_code == 200, feedback.text
    assert feedback.json()["user_satisfaction_score"] == 5


def test_issue_attachment_binding_and_download_scope(
    client: TestClient,
    auth_headers: Callable[[str, str], dict[str, str]],
) -> None:
    add_extra_user()
    user_headers = auth_headers("user", "user123")
    user2_headers = auth_headers("pytest_user2", "user2123")
    admin_headers = auth_headers("admin", "admin123")

    upload = client.post(
        "/api/issues/attachments",
        headers=user_headers,
        files={"file": ("vpn.log", b"certificate expired", "text/plain")},
    )
    assert upload.status_code == 200, upload.text
    attachment_url = upload.json()["url"]

    other_download = client.get(attachment_url, headers=user2_headers)
    assert other_download.status_code == 403, other_download.text

    hijack = client.post("/api/issues", headers=user2_headers, json=issue_payload("pytest_hijack_attachment", attachment_url))
    assert hijack.status_code == 403, hijack.text

    issue_id = create_issue(client, user_headers, "pytest_attachment_issue", attachment_url)
    owner_download = client.get(attachment_url, headers=user_headers)
    assert owner_download.status_code == 200, owner_download.text
    assert owner_download.content == b"certificate expired"

    other_bound_download = client.get(attachment_url, headers=user2_headers)
    assert other_bound_download.status_code == 403, other_bound_download.text

    admin_download = client.get(attachment_url, headers=admin_headers)
    assert admin_download.status_code == 200, admin_download.text

    listing = client.get("/api/issues", headers=user_headers, params={"q": "pytest_attachment_issue"})
    assert listing.status_code == 200, listing.text
    assert listing.json()[0]["id"] == issue_id


def test_user_cannot_process_or_feedback_other_issue(
    client: TestClient,
    auth_headers: Callable[[str, str], dict[str, str]],
) -> None:
    add_extra_user("pytest_user3")
    user_headers = auth_headers("user", "user123")
    user3_headers = auth_headers("pytest_user3", "user2123")
    issue_id = create_issue(client, user_headers, "pytest_permission_issue")

    accept = client.post(f"/api/issues/{issue_id}/accept", headers=user_headers)
    assert accept.status_code == 403, accept.text

    feedback = client.post(
        f"/api/issues/{issue_id}/feedback",
        headers=user3_headers,
        json={"feedback": "不是我的记录", "satisfaction_score": 4},
    )
    assert feedback.status_code == 403, feedback.text
    assert "只能评价自己提交的在线记录" in feedback.json()["detail"]
