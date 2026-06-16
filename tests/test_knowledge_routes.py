from __future__ import annotations

from typing import Callable

from fastapi.testclient import TestClient


def knowledge_payload(title: str, status: str = "pending_review", content: str | None = None) -> dict[str, str]:
    return {
        "content": content or f"{title} 的标准处理步骤。",
        "source_type": "faq",
        "status": status,
        "tags": "pytest,knowledge",
        "title": title,
    }


def create_knowledge(client: TestClient, headers: dict[str, str], title: str, status: str = "pending_review") -> dict[str, object]:
    response = client.post("/api/knowledge", headers=headers, json=knowledge_payload(title, status))
    assert response.status_code == 200, response.text
    return response.json()


def test_admin_can_create_and_list_pending_knowledge(
    client: TestClient,
    auth_headers: Callable[[str, str], dict[str, str]],
) -> None:
    admin_headers = auth_headers("admin", "admin123")
    created = create_knowledge(client, admin_headers, "pytest_admin_pending_knowledge")

    assert created["status"] == "pending_review"
    assert created["version"] == 1
    assert created["sensitive_check"]["has_sensitive"] is False

    listing = client.get(
        "/api/knowledge",
        headers=admin_headers,
        params={"q": "pytest_admin_pending_knowledge", "status": "pending_review"},
    )
    assert listing.status_code == 200, listing.text
    rows = listing.json()
    assert rows[0]["id"] == created["id"]
    assert rows[0]["sensitive_check"]["has_sensitive"] is False


def test_ops_create_forces_pending_review(
    client: TestClient,
    auth_headers: Callable[[str, str], dict[str, str]],
) -> None:
    ops_headers = auth_headers("ops", "ops123")
    created = create_knowledge(client, ops_headers, "pytest_ops_forced_pending_knowledge", "published")

    assert created["status"] == "pending_review"
    assert created["reviewed_by"] is None


def test_admin_publish_blocks_sensitive_content(
    client: TestClient,
    auth_headers: Callable[[str, str], dict[str, str]],
) -> None:
    admin_headers = auth_headers("admin", "admin123")
    created = client.post(
        "/api/knowledge",
        headers=admin_headers,
        json=knowledge_payload(
            "pytest_sensitive_pending_knowledge",
            content="处理时不要写入用户电话 13800138000，也不要暴露 password=abc123。",
        ),
    )
    assert created.status_code == 200, created.text

    publish = client.post(
        f"/api/knowledge/{created.json()['id']}/status",
        headers=admin_headers,
        json={"review_note": "pytest publish", "status": "published"},
    )
    assert publish.status_code == 400, publish.text
    assert "高风险敏感信息" in publish.json()["detail"]["message"]
    assert publish.json()["detail"]["sensitive_check"]["blocking"] is True


def test_sensitive_check_redacts_phone_and_credential(
    client: TestClient,
    auth_headers: Callable[[str, str], dict[str, str]],
) -> None:
    admin_headers = auth_headers("admin", "admin123")
    response = client.post(
        "/api/knowledge/sensitive-check",
        headers=admin_headers,
        json={
            "content": "联系电话 13800138000，临时 password=abc123。",
            "tags": "pytest",
            "title": "pytest_sensitive_check",
        },
    )
    assert response.status_code == 200, response.text
    payload = response.json()

    assert payload["blocking"] is True
    assert "[手机号已脱敏]" in payload["redacted"]["content"]
    assert "[敏感凭据已脱敏]" in payload["redacted"]["content"]
    assert {item["type"] for item in payload["findings"]} >= {"credential", "mainland_phone"}


def test_document_upload_splits_redacts_and_enters_rag_after_publish(
    client: TestClient,
    auth_headers: Callable[[str, str], dict[str, str]],
) -> None:
    admin_headers = auth_headers("admin", "admin123")
    content = "\n\n".join(
        [
            "# VPN 证书续期手册",
            "适用范围：远程办公 VPN 客户端提示 certificate expired。",
            "处理步骤：在运维堡垒机执行 restart-vpn-cert 任务，完成后通知用户重新登录。",
            "回访说明：原始联系人 13800138000，临时 password=abc123 不能进入知识库。",
        ]
    )

    uploaded = client.post(
        "/api/knowledge/documents/upload",
        data={"tags": "VPN,证书", "title": "VPN 证书续期手册"},
        files={"file": ("vpn-runbook.md", content.encode("utf-8"), "text/markdown")},
        headers=admin_headers,
    )
    assert uploaded.status_code == 200, uploaded.text
    upload_body = uploaded.json()
    assert upload_body["chunk_count"] >= 1
    assert upload_body["redacted_count"] >= 1
    assert upload_body["chunks"][0]["source_type"] == "document"
    assert upload_body["chunks"][0]["status"] == "pending_review"
    assert upload_body["chunks"][0]["sensitive_check"]["blocking"] is True

    listing = client.get(
        "/api/knowledge",
        headers=admin_headers,
        params={"q": "restart-vpn-cert", "source_type": "document", "status": "pending_review"},
    )
    assert listing.status_code == 200, listing.text
    rows = listing.json()
    assert rows
    imported = rows[0]
    assert imported["source_type"] == "document"
    assert imported["status"] == "pending_review"
    assert "13800138000" not in imported["content"]
    assert "password=abc123" not in imported["content"]
    assert "[手机号已脱敏]" in imported["content"]
    assert "[敏感凭据已脱敏]" in imported["content"]
    assert imported["sensitive_check"]["blocking"] is False

    publish = client.post(
        f"/api/knowledge/{imported['id']}/status",
        headers=admin_headers,
        json={"review_note": "文档导入脱敏后发布", "status": "published"},
    )
    assert publish.status_code == 200, publish.text

    ask = client.post(
        "/api/qa/ask",
        headers=admin_headers,
        json={"question": "VPN certificate expired 时 restart-vpn-cert 应该怎么处理？"},
    )
    assert ask.status_code == 200, ask.text
    references = ask.json()["references"]
    assert any(item["id"] == imported["id"] and item["source_type"] == "document" for item in references)


def test_rag_index_refreshes_after_published_knowledge_update(
    client: TestClient,
    auth_headers: Callable[[str, str], dict[str, str]],
) -> None:
    admin_headers = auth_headers("admin", "admin123")
    created = create_knowledge(
        client,
        admin_headers,
        "pytest_rag_cache_legacy",
        "published",
    )

    from backend.app.services.qa_service import rag_service

    with_old_content = client.put(
        f"/api/knowledge/{created['id']}",
        headers=admin_headers,
        json=knowledge_payload("pytest_rag_cache_legacy", "published", "legacycachealpha 标准处理步骤。"),
    )
    assert with_old_content.status_code == 200, with_old_content.text

    old_result = rag_service.search("legacycachealpha", limit=3)
    assert any(item["id"] == created["id"] for item in old_result.references)

    with_new_content = client.put(
        f"/api/knowledge/{created['id']}",
        headers=admin_headers,
        json=knowledge_payload("pytest_rag_cache_modern", "published", "moderncachebeta 标准处理步骤。"),
    )
    assert with_new_content.status_code == 200, with_new_content.text

    new_result = rag_service.search("moderncachebeta", limit=3)
    stale_result = rag_service.search("legacycachealpha", limit=3)
    assert any(item["id"] == created["id"] for item in new_result.references)
    assert not any(item["id"] == created["id"] for item in stale_result.references)


def test_user_cannot_upload_knowledge_document(
    client: TestClient,
    auth_headers: Callable[[str, str], dict[str, str]],
) -> None:
    user_headers = auth_headers("user", "user123")
    response = client.post(
        "/api/knowledge/documents/upload",
        data={"tags": "VPN", "title": "普通用户上传"},
        files={"file": ("note.txt", b"plain text", "text/plain")},
        headers=user_headers,
    )
    assert response.status_code == 403, response.text


def test_user_listing_only_returns_published_knowledge(
    client: TestClient,
    auth_headers: Callable[[str, str], dict[str, str]],
) -> None:
    admin_headers = auth_headers("admin", "admin123")
    user_headers = auth_headers("user", "user123")
    create_knowledge(client, admin_headers, "pytest_user_visibility_pending", "pending_review")
    published = create_knowledge(client, admin_headers, "pytest_user_visibility_published", "published")

    listing = client.get("/api/knowledge", headers=user_headers, params={"q": "pytest_user_visibility"})
    assert listing.status_code == 200, listing.text
    rows = listing.json()

    assert [row["id"] for row in rows] == [published["id"]]
    assert rows[0]["status"] == "published"
    assert "sensitive_check" not in rows[0]


def test_ops_cannot_update_published_knowledge(
    client: TestClient,
    auth_headers: Callable[[str, str], dict[str, str]],
) -> None:
    admin_headers = auth_headers("admin", "admin123")
    ops_headers = auth_headers("ops", "ops123")
    published = create_knowledge(client, admin_headers, "pytest_ops_update_published", "published")

    update = client.put(
        f"/api/knowledge/{published['id']}",
        headers=ops_headers,
        json=knowledge_payload("pytest_ops_update_published_changed", "pending_review"),
    )
    assert update.status_code == 403, update.text
    assert "运维人员只能维护待审核知识候选" in update.json()["detail"]
