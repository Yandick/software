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


def test_duplicate_check_warns_and_blocks_exact_duplicate(
    client: TestClient,
    auth_headers: Callable[[str, str], dict[str, str]],
) -> None:
    admin_headers = auth_headers("admin", "admin123")
    content = "pytestduplicatetoken VPN 证书续期处理：先核对证书到期时间，再执行受控续期任务，最后通知用户重新登录。"
    created = create_knowledge(
        client,
        admin_headers,
        "pytest_duplicate_vpn_cert_base",
        "published",
    )
    updated = client.put(
        f"/api/knowledge/{created['id']}",
        headers=admin_headers,
        json=knowledge_payload("pytest_duplicate_vpn_cert_base", "published", content),
    )
    assert updated.status_code == 200, updated.text

    exact_check = client.post(
        "/api/knowledge/duplicate-check",
        headers=admin_headers,
        json={
            "content": content,
            "tags": "pytest,VPN,证书",
            "title": "pytest_duplicate_vpn_cert_copy",
        },
    )
    assert exact_check.status_code == 200, exact_check.text
    exact_body = exact_check.json()
    assert exact_body["blocking"] is True
    assert exact_body["decision"] == "exact_duplicate"
    assert exact_body["candidates"][0]["id"] == created["id"]
    assert exact_body["candidates"][0]["relation"] == "exact_content"

    duplicate = client.post(
        "/api/knowledge",
        headers=admin_headers,
        json=knowledge_payload("pytest_duplicate_vpn_cert_copy", "pending_review", content),
    )
    assert duplicate.status_code == 409, duplicate.text
    assert duplicate.json()["detail"]["duplicate_check"]["decision"] == "exact_duplicate"

    near_check = client.post(
        "/api/knowledge/duplicate-check",
        headers=admin_headers,
        json={
            "content": f"{content} 适用范围补充：只用于 pytestduplicatetoken 测试环境，不覆盖生产证书审批。",
            "tags": "pytest,VPN,证书",
            "title": "pytest_duplicate_vpn_cert_near",
        },
    )
    assert near_check.status_code == 200, near_check.text
    near_body = near_check.json()
    assert near_body["blocking"] is False
    assert near_body["decision"] == "near_duplicate"
    assert near_body["candidates"][0]["id"] == created["id"]


def test_duplicate_check_detects_semantic_alias_and_diff_summary(
    client: TestClient,
    auth_headers: Callable[[str, str], dict[str, str]],
) -> None:
    admin_headers = auth_headers("admin", "admin123")
    base = client.post(
        "/api/knowledge",
        headers=admin_headers,
        json=knowledge_payload(
            "pytest_mfa_alias_base",
            "published",
            "多因素认证验证码无法接收时，先确认手机时间同步，再检查认证器绑定状态。",
        )
        | {"tags": "MFA,验证码"},
    )
    assert base.status_code == 200, base.text

    check = client.post(
        "/api/knowledge/duplicate-check",
        headers=admin_headers,
        json={
            "content": "用户收不到 MFA 短信验证码，需检查多因素认证绑定和手机验证状态。",
            "tags": "二次验证,手机验证",
            "title": "二次验证手机验证失败",
        },
    )
    assert check.status_code == 200, check.text
    body = check.json()
    assert body["decision"] == "near_duplicate"
    candidate = body["candidates"][0]
    assert candidate["id"] == base.json()["id"]
    assert candidate["domain_similarity"] >= 0.67
    assert candidate["approx_similarity"] > 0
    assert candidate["diff"]["semantic_relation"] == "same_problem_new_solution"
    assert candidate["diff"]["recommended_action"] == "merge_candidate"


def test_autonomous_ingest_skips_exact_duplicate_without_creating_row(
    client: TestClient,
    auth_headers: Callable[[str, str], dict[str, str]],
) -> None:
    admin_headers = auth_headers("admin", "admin123")
    content = "pytestautoskiptok VPN 自动去重：证书到期后执行续期任务，并通知用户重新登录。"
    created = create_knowledge(client, admin_headers, "pytest_auto_skip_base", "published")
    updated = client.put(
        f"/api/knowledge/{created['id']}",
        headers=admin_headers,
        json=knowledge_payload("pytest_auto_skip_base", "published", content),
    )
    assert updated.status_code == 200, updated.text

    before = client.get("/api/knowledge", headers=admin_headers, params={"q": "pytestautoskiptok"})
    assert before.status_code == 200, before.text
    before_count = len(before.json())

    auto = client.post(
        "/api/knowledge/autonomous-ingest",
        headers=admin_headers,
        json=knowledge_payload("pytest_auto_skip_copy", "published", content),
    )
    assert auto.status_code == 200, auto.text
    body = auto.json()
    assert body["action"] == "skipped_exact_duplicate"
    assert body["item"]["id"] == created["id"]

    after = client.get("/api/knowledge", headers=admin_headers, params={"q": "pytestautoskiptok"})
    assert after.status_code == 200, after.text
    assert len(after.json()) == before_count


def test_autonomous_ingest_merges_near_duplicate_into_published_for_admin(
    client: TestClient,
    auth_headers: Callable[[str, str], dict[str, str]],
) -> None:
    admin_headers = auth_headers("admin", "admin123")
    base_content = "pytestautomergenet VPN 自动合并：先核验证书到期时间，再执行受控续期任务，最后通知用户重新登录。"
    extra = "新增验证：pytestautomergeextra 完成后检查客户端证书序列号已经刷新。"
    created = create_knowledge(client, admin_headers, "pytest_auto_merge_base", "published")
    updated = client.put(
        f"/api/knowledge/{created['id']}",
        headers=admin_headers,
        json=knowledge_payload("pytest_auto_merge_base", "published", base_content),
    )
    assert updated.status_code == 200, updated.text

    auto = client.post(
        "/api/knowledge/autonomous-ingest",
        headers=admin_headers,
        json=knowledge_payload("pytest_auto_merge_near", "published", f"{base_content}{extra}"),
    )
    assert auto.status_code == 200, auto.text
    body = auto.json()
    assert body["action"] == "merged_existing"
    assert body["item"]["id"] == created["id"]
    assert extra in body["novel_units"]

    listing = client.get("/api/knowledge", headers=admin_headers, params={"q": "pytestautomergeextra", "status": "published"})
    assert listing.status_code == 200, listing.text
    rows = listing.json()
    assert [row["id"] for row in rows] == [created["id"]]
    assert "自动合并" in rows[0]["content"]
    assert "pytestautomergeextra" in rows[0]["content"]


def test_autonomous_ingest_ops_creates_pending_merge_candidate_for_published_duplicate(
    client: TestClient,
    auth_headers: Callable[[str, str], dict[str, str]],
) -> None:
    admin_headers = auth_headers("admin", "admin123")
    ops_headers = auth_headers("ops", "ops123")
    base_content = "pytestautocandidate VPN 自动候选：先检查客户端版本，再检查证书状态。"
    extra = "补充步骤：pytestautocandidateextra 若客户端版本过低，先升级再重试连接。"
    created = create_knowledge(client, admin_headers, "pytest_auto_candidate_base", "published")
    updated = client.put(
        f"/api/knowledge/{created['id']}",
        headers=admin_headers,
        json=knowledge_payload("pytest_auto_candidate_base", "published", base_content),
    )
    assert updated.status_code == 200, updated.text

    auto = client.post(
        "/api/knowledge/autonomous-ingest",
        headers=ops_headers,
        json=knowledge_payload("pytest_auto_candidate_near", "published", f"{base_content}{extra}"),
    )
    assert auto.status_code == 200, auto.text
    body = auto.json()
    assert body["action"] == "inserted_merge_candidate"
    assert body["item"]["status"] == "pending_review"
    assert body["item"]["title"].startswith("自动合并候选：")

    published = client.get("/api/knowledge", headers=admin_headers, params={"q": "pytestautocandidateextra", "status": "published"})
    pending = client.get("/api/knowledge", headers=admin_headers, params={"q": "pytestautocandidateextra", "status": "pending_review"})
    assert published.status_code == 200, published.text
    assert pending.status_code == 200, pending.text
    assert published.json() == []
    assert pending.json()[0]["id"] == body["item"]["id"]


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


def test_document_upload_skips_exact_duplicate_chunks(
    client: TestClient,
    auth_headers: Callable[[str, str], dict[str, str]],
) -> None:
    admin_headers = auth_headers("admin", "admin123")
    content = "pytestdocduptoken 文档导入重复片段：确认故障现象、影响范围和回访结果后再沉淀知识。"
    create_knowledge(
        client,
        admin_headers,
        "pytest_document_duplicate_base",
        "published",
    )
    rows = client.get(
        "/api/knowledge",
        headers=admin_headers,
        params={"q": "pytest_document_duplicate_base", "status": "published"},
    )
    base_id = rows.json()[0]["id"]
    updated = client.put(
        f"/api/knowledge/{base_id}",
        headers=admin_headers,
        json=knowledge_payload("pytest_document_duplicate_base", "published", content),
    )
    assert updated.status_code == 200, updated.text

    uploaded = client.post(
        "/api/knowledge/documents/upload",
        data={"tags": "pytest,重复", "title": "pytest_document_duplicate_upload"},
        files={"file": ("duplicate.md", content.encode("utf-8"), "text/markdown")},
        headers=admin_headers,
    )
    assert uploaded.status_code == 200, uploaded.text
    upload_body = uploaded.json()
    assert upload_body["chunk_count"] == 0
    assert upload_body["skipped_count"] == 1
    assert upload_body["skipped_chunks"][0]["duplicate_check"]["decision"] == "exact_duplicate"


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
