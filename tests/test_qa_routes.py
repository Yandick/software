from __future__ import annotations

from typing import Callable

from fastapi.testclient import TestClient


def ask(client: TestClient, headers: dict[str, str], question: str, conversation_id: int | None = None) -> dict[str, object]:
    payload: dict[str, object] = {"question": question}
    if conversation_id:
        payload["conversation_id"] = conversation_id
    response = client.post("/api/qa/ask", headers=headers, json=payload)
    assert response.status_code == 200, response.text
    return response.json()


def test_qa_ask_creates_conversation_and_messages(
    client: TestClient,
    auth_headers: Callable[[str, str], dict[str, str]],
) -> None:
    user_headers = auth_headers("user", "user123")
    answer = ask(client, user_headers, "VPN 提示证书过期，远程办公无法连接，电话 13800138000")

    assert answer["conversation_id"]
    assert answer["employee"]["name"] == "云维"
    assert answer["model_status"] == "fake-vllm"
    assert answer["rag"]["strategy"] == "hybrid_keyword_chunk"
    assert answer["issue_draft"]["category"] == "network"
    assert answer["issue_draft"]["extraction_source"] == "llm"
    assert answer["issue_draft"]["contact_phone"] == "13800138000"
    assert "联系方式" not in answer["missing_fields"]
    assert answer["agent"]["mode"] == "controlled_react_prototype"
    trace = answer["agent"]["trace"]
    assert [step["phase"] for step in trace] == ["Reason", "Act", "Act", "Act", "Final"]
    assert [step["tool"] for step in trace[:4]] == ["planner", "knowledge_search", "issue_draft", "handoff_script"]
    assert trace[-1]["tool"] == "planner"
    assert trace[1]["observation"]["reference_count"] >= 1
    assert trace[2]["observation"]["extraction_source"] == "llm"

    detail = client.get(f"/api/qa/conversations/{answer['conversation_id']}", headers=user_headers)
    assert detail.status_code == 200, detail.text
    messages = detail.json()["messages"]
    assert [item["role"] for item in messages] == ["user", "assistant"]
    assert messages[1]["metadata"]["agent"]["trace"][1]["tool"] == "knowledge_search"
    assert messages[1]["metadata"]["issue_draft"]["category"] == "network"


def test_qa_conversation_scope_and_readonly_rules(
    client: TestClient,
    auth_headers: Callable[[str, str], dict[str, str]],
) -> None:
    user_headers = auth_headers("user", "user123")
    admin_headers = auth_headers("admin", "admin123")
    conversation_id = int(ask(client, user_headers, "账号被冻结提示锁定，怎么恢复登录")["conversation_id"])

    admin_read = client.get(f"/api/qa/conversations/{conversation_id}", headers=admin_headers)
    assert admin_read.status_code == 200, admin_read.text

    admin_write = client.post(
        "/api/qa/ask",
        headers=admin_headers,
        json={"conversation_id": conversation_id, "question": "管理员尝试继续这个会话"},
    )
    assert admin_write.status_code == 403, admin_write.text
    assert "只读" in admin_write.json()["detail"]


def test_user_conversation_list_only_returns_own_records(
    client: TestClient,
    auth_headers: Callable[[str, str], dict[str, str]],
) -> None:
    user_headers = auth_headers("user", "user123")
    admin_headers = auth_headers("admin", "admin123")
    user_conversation_id = int(ask(client, user_headers, "邮箱客户端无法收信怎么处理")["conversation_id"])
    admin_conversation_id = int(ask(client, admin_headers, "数据库连接失败需要先排查什么")["conversation_id"])

    listing = client.get("/api/qa/conversations", headers=user_headers)
    assert listing.status_code == 200, listing.text
    ids = {item["id"] for item in listing.json()}

    assert user_conversation_id in ids
    assert admin_conversation_id not in ids


def test_rag_evaluate_and_suggest_permissions(
    client: TestClient,
    auth_headers: Callable[[str, str], dict[str, str]],
) -> None:
    auditor_headers = auth_headers("auditor", "audit123")
    user_headers = auth_headers("user", "user123")

    suggest = client.get("/api/qa/suggest", headers=user_headers, params={"q": "VPN", "limit": 3})
    assert suggest.status_code == 200, suggest.text
    assert suggest.json()
    assert "query" in suggest.json()[0]

    evaluate = client.get("/api/rag/evaluate", headers=auditor_headers)
    assert evaluate.status_code == 200, evaluate.text
    assert evaluate.json()["total"] == 5
    assert evaluate.json()["passed"] >= 1

    forbidden = client.get("/api/rag/evaluate", headers=user_headers)
    assert forbidden.status_code == 403, forbidden.text
