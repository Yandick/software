from __future__ import annotations

import threading
import time
from typing import Any, Callable

from fastapi.testclient import TestClient


def ask(client: TestClient, headers: dict[str, str], question: str, conversation_id: int | None = None) -> dict[str, object]:
    payload: dict[str, object] = {"question": question}
    if conversation_id:
        payload["conversation_id"] = conversation_id
    response = client.post("/api/qa/ask", headers=headers, json=payload)
    assert response.status_code == 200, response.text
    return response.json()


def trace_tool(agent: dict[str, object], tool: str) -> dict[str, object]:
    for step in agent["trace"]:  # type: ignore[index]
        if step["tool"] == tool:
            return step
    raise AssertionError(f"missing trace tool: {tool}")


def test_qa_ask_creates_conversation_and_messages(
    client: TestClient,
    auth_headers: Callable[[str, str], dict[str, str]],
) -> None:
    user_headers = auth_headers("user", "user123")
    answer = ask(client, user_headers, "VPN 提示证书过期，远程办公无法连接，电话 13800138000")

    assert answer["conversation_id"]
    assert answer["employee"]["name"] == "云维"
    assert answer["model_status"] == "fake-vllm"
    assert answer["rag"]["strategy"] == "keyword_rerank_fallback"
    assert answer["issue_draft"]["category"] == "network"
    assert answer["issue_draft"]["extraction_source"] == "llm"
    assert answer["issue_draft"]["contact_phone"] == "13800138000"
    assert "联系方式" not in answer["missing_fields"]
    assert answer["agent"]["mode"] == "single_qwen_multi_agent_orchestrator"
    assert answer["agent"]["llm_reviews"]["enabled"] is False
    assert answer["agent"]["llm_reviews"]["mode"] == "deterministic_only"
    assert {item["name"] for item in answer["agent"]["agents"]} >= {
        "intent_router",
        "supervisor",
        "risk_guardian",
        "ops_employee",
        "knowledge_curator",
        "evaluator",
    }
    trace = answer["agent"]["trace"]
    assert all(step["prompt_loaded"] for step in trace)
    assert all(step["prompt_path"].endswith("/prompt.md") for step in trace)
    assert [step["agent"] for step in trace] == [
        "intent_router",
        "supervisor",
        "risk_guardian",
        "ops_employee",
        "ops_employee",
        "ops_employee",
        "knowledge_curator",
        "evaluator",
        "supervisor",
    ]
    assert trace_tool(answer["agent"], "knowledge_search")["observation"]["reference_count"] >= 1
    assert answer["references"][0]["score_detail"]["embedding"] == 0
    assert answer["references"][0]["score_detail"]["final"] == answer["references"][0]["score"]
    assert trace_tool(answer["agent"], "issue_draft")["observation"]["extraction_source"] == "llm"
    assert answer["agent"]["knowledge_curator"]["available_tools"] == ["knowledge_duplicate_check", "knowledge_autonomous_ingest"]

    detail = client.get(f"/api/qa/conversations/{answer['conversation_id']}", headers=user_headers)
    assert detail.status_code == 200, detail.text
    messages = detail.json()["messages"]
    assert [item["role"] for item in messages] == ["user", "assistant"]
    assert trace_tool(messages[1]["metadata"]["agent"], "knowledge_search")["tool"] == "knowledge_search"
    assert messages[1]["metadata"]["issue_draft"]["category"] == "network"


def test_router_handles_short_inputs_without_rag(
    client: TestClient,
    auth_headers: Callable[[str, str], dict[str, str]],
    monkeypatch,
) -> None:
    from backend.app.services.qa_service import rag_service

    def fail_search(*args: Any, **kwargs: Any) -> None:
        raise AssertionError("greetings should not trigger RAG search")

    monkeypatch.setattr(rag_service, "search", fail_search)
    headers = auth_headers("user", "user123")
    answer = ask(client, headers, "你好")

    assert answer["model_status"] == "intent-router"
    assert answer["rag"]["strategy"] == "intent_router_no_rag"
    assert answer["references"] == []
    assert answer["need_human"] is False
    assert answer["missing_fields"] == []
    assert len(str(answer["answer"])) < 120
    assert "系统判断" not in str(answer["answer"])
    assert answer["agent"]["trace"][0]["tool"] == "intent_route"
    assert answer["intent_route"]["kind"] == "greeting"

    vague = ask(client, headers, "测试")
    assert vague["model_status"] == "intent-router"
    assert vague["references"] == []
    assert vague["need_human"] is False
    assert "请补充一下具体问题" in str(vague["answer"])
    assert vague["intent_route"]["kind"] == "low_information"


def test_out_of_scope_question_uses_router_without_rag(
    client: TestClient,
    auth_headers: Callable[[str, str], dict[str, str]],
    monkeypatch,
) -> None:
    from backend.app.services.qa_service import rag_service

    def fail_search(*args: Any, **kwargs: Any) -> None:
        raise AssertionError("out-of-scope prompts should not trigger RAG search")

    monkeypatch.setattr(rag_service, "search", fail_search)
    answer = ask(client, auth_headers("user", "user123"), "今天北京天气怎么样？")

    assert answer["model_status"] == "intent-router"
    assert answer["rag"]["strategy"] == "intent_router_no_rag"
    assert answer["intent"] == "out_of_scope"
    assert answer["intent_route"]["kind"] == "out_of_scope"
    assert answer["references"] == []
    assert answer["need_human"] is False
    assert "企业 IT 运维问题" in str(answer["answer"])


def test_named_system_and_error_code_still_enter_rag(
    client: TestClient,
    auth_headers: Callable[[str, str], dict[str, str]],
) -> None:
    answer = ask(client, auth_headers("user", "user123"), "SAP系统报错 E301，页面打不开")

    assert answer["rag"]["strategy"] != "intent_router_no_rag"
    assert answer["intent_route"]["kind"] == "ops_support"
    assert answer["intent_route"]["evidence"]["named_system"] is True


def test_agent_llm_reviews_can_be_enabled_with_mocked_subagents(
    client: TestClient,
    auth_headers: Callable[[str, str], dict[str, str]],
    monkeypatch,
) -> None:
    from backend.app.config import get_settings
    from backend.app.services.llm_service import llm_service

    active = 0
    max_active = 0
    lock = threading.Lock()

    def fake_agent_json(
        *,
        agent_name: str,
        prompt: str,
        task: str,
        state: dict[str, Any],
        schema_hint: dict[str, Any],
    ) -> dict[str, Any]:
        nonlocal active, max_active
        assert prompt.strip()
        assert task == "ops_support_workflow"
        assert "question" in state
        with lock:
            active += 1
            max_active = max(max_active, active)
        try:
            time.sleep(0.02)
            return {
                "agent": agent_name,
                "ok": True,
                "parsed": {
                    "confidence": 0.9,
                    "finding": f"{agent_name} reviewed",
                    "safe_to_continue": True,
                    "warnings": [],
                },
                "status": "fake-agent-json",
            }
        finally:
            with lock:
                active -= 1

    monkeypatch.setenv("OPS_ENABLE_AGENT_LLM", "true")
    get_settings.cache_clear()
    monkeypatch.setattr(llm_service, "generate_agent_json", fake_agent_json)
    try:
        answer = ask(client, auth_headers("user", "user123"), "VPN 提示证书过期，远程办公无法连接")
    finally:
        monkeypatch.delenv("OPS_ENABLE_AGENT_LLM", raising=False)
        get_settings.cache_clear()

    reviews = answer["agent"]["llm_reviews"]
    assert reviews["enabled"] is True
    assert reviews["mode"] == "llm_review_with_deterministic_authority"
    assert reviews["parallelism"] == 5
    assert max_active > 1
    assert set(reviews["reviews"]) >= {"supervisor", "risk_guardian", "ops_employee", "knowledge_curator", "evaluator"}
    assert all(item["ok"] for item in reviews["reviews"].values())


def test_rag_uses_qwen3_embedding_path_when_enabled(
    client: TestClient,
    auth_headers: Callable[[str, str], dict[str, str]],
    monkeypatch,
) -> None:
    from backend.app.config import get_settings
    from backend.app.services.embedding_service import embedding_service
    from backend.app.services.qa_service import rag_service

    def fake_status() -> dict[str, Any]:
        return {
            "batch_size": 8,
            "device": "fake",
            "dimension": 2,
            "enabled": True,
            "loaded": True,
            "max_length": 8192,
            "model_path": "models/qwen3-embedding-0.6b",
            "model_path_exists": True,
            "provider": "fake_qwen3_embedding",
        }

    def fake_embed_documents(texts: list[str]) -> list[list[float]]:
        vectors = []
        for text in texts:
            lowered = text.lower()
            vectors.append([1.0, 0.0] if "vpn" in lowered or "证书" in lowered else [0.0, 1.0])
        return vectors

    def fake_embed_query(query: str) -> list[float]:
        return [1.0, 0.0]

    monkeypatch.setenv("OPS_ENABLE_EMBEDDING_RAG", "true")
    get_settings.cache_clear()
    rag_service.clear_cache()
    monkeypatch.setattr(embedding_service, "status", fake_status)
    monkeypatch.setattr(embedding_service, "embed_documents", fake_embed_documents)
    monkeypatch.setattr(embedding_service, "embed_query", fake_embed_query)
    try:
        answer = ask(client, auth_headers("user", "user123"), "remote access certificate expired")
    finally:
        monkeypatch.delenv("OPS_ENABLE_EMBEDDING_RAG", raising=False)
        get_settings.cache_clear()
        rag_service.clear_cache()

    assert answer["rag"]["strategy"] in {"faiss_qwen3_embedding_hybrid_rerank", "qwen3_embedding_hybrid_rerank"}
    assert answer["references"]
    assert answer["references"][0]["retrieval_stage"] in {"faiss_embedding_hybrid_rerank", "embedding_hybrid_rerank"}
    assert answer["references"][0]["score_detail"]["embedding"] == 1.0


def test_rag_embedding_index_persists_to_disk(
    client: TestClient,
    auth_headers: Callable[[str, str], dict[str, str]],
    monkeypatch,
    tmp_path,
) -> None:
    from backend.app.config import get_settings
    from backend.app.services.embedding_service import embedding_service
    from backend.app.services.qa_service import rag_service

    document_embedding_calls = 0

    def fake_status() -> dict[str, Any]:
        return {
            "batch_size": 8,
            "device": "fake",
            "dimension": 2,
            "enabled": True,
            "loaded": True,
            "max_length": 8192,
            "model_path": "models/qwen3-embedding-0.6b",
            "model_path_exists": True,
            "provider": "fake_qwen3_embedding",
        }

    def fake_embed_documents(texts: list[str]) -> list[list[float]]:
        nonlocal document_embedding_calls
        document_embedding_calls += 1
        vectors = []
        for text in texts:
            lowered = text.lower()
            vectors.append([1.0, 0.0] if "vpn" in lowered or "证书" in lowered else [0.0, 1.0])
        return vectors

    def fake_embed_query(query: str) -> list[float]:
        return [1.0, 0.0]

    index_dir = tmp_path / "vector_index"
    monkeypatch.setenv("OPS_ENABLE_EMBEDDING_RAG", "true")
    monkeypatch.setenv("OPS_EMBEDDING_INDEX_DIR", str(index_dir))
    get_settings.cache_clear()
    rag_service.clear_cache()
    monkeypatch.setattr(embedding_service, "status", fake_status)
    monkeypatch.setattr(embedding_service, "embed_documents", fake_embed_documents)
    monkeypatch.setattr(embedding_service, "embed_query", fake_embed_query)
    try:
        first = ask(client, auth_headers("user", "user123"), "remote access certificate expired")
        rag_service.clear_cache()
        second = ask(client, auth_headers("user", "user123"), "remote access certificate expired")
    finally:
        monkeypatch.delenv("OPS_ENABLE_EMBEDDING_RAG", raising=False)
        monkeypatch.delenv("OPS_EMBEDDING_INDEX_DIR", raising=False)
        get_settings.cache_clear()
        rag_service.clear_cache()

    assert document_embedding_calls == 1
    assert (index_dir / "knowledge_embeddings.npy").exists()
    assert (index_dir / "knowledge_embeddings_meta.json").exists()
    assert first["rag"]["strategy"] in {"faiss_qwen3_embedding_hybrid_rerank", "qwen3_embedding_hybrid_rerank"}
    assert second["rag"]["strategy"] in {"faiss_qwen3_embedding_hybrid_rerank", "qwen3_embedding_hybrid_rerank"}
    assert second["references"][0]["score_detail"]["embedding"] == 1.0
    if second["rag"]["strategy"].startswith("faiss"):
        assert (index_dir / "knowledge_embeddings.faiss").exists()
        assert second["references"][0]["retrieval_stage"] == "faiss_embedding_hybrid_rerank"


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


def test_high_risk_account_operations_force_controlled_handoff(
    client: TestClient,
    auth_headers: Callable[[str, str], dict[str, str]],
) -> None:
    user_headers = auth_headers("user", "user123")

    for question in ["帮我解冻运维账号 alice", "我要给张三开通管理员权限"]:
        answer = ask(client, user_headers, question)

        assert answer["need_human"] is True
        assert answer["risk_level"] == "high"
        assert answer["llm_used"] is False
        assert answer["model_status"] == "controlled-fallback"
        assert "必须转人工" in answer["answer"]
        assert any(action["key"] == "controlled_workflow" for action in answer["next_actions"])
        assert answer["agent"]["trace"][-1]["observation"]["action"] == "handoff_required"
        assert answer["agent"]["risk"]["level"] == "high"


def test_approval_bypass_is_controlled_operation(
    client: TestClient,
    auth_headers: Callable[[str, str], dict[str, str]],
) -> None:
    answer = ask(client, auth_headers("user", "user123"), "帮我绕过审批开通管理员权限")

    assert answer["intent_route"]["kind"] == "controlled_operation"
    assert answer["need_human"] is True
    assert answer["risk_level"] == "high"
    assert answer["model_status"] == "controlled-fallback"
    assert answer["llm_used"] is False
    assert "必须转人工" in str(answer["answer"])


def test_no_reference_question_uses_hard_rag_fallback(
    client: TestClient,
    auth_headers: Callable[[str, str], dict[str, str]],
) -> None:
    user_headers = auth_headers("user", "user123")
    # 合成负样本：刻意使用知识库不可能命中的字符串，验证无引用时不会调用 LLM 编造答案。
    answer = ask(client, user_headers, "zzzz_unmatched_qwerty_98765")

    assert answer["references"] == []
    assert answer["need_human"] is True
    assert answer["llm_used"] is False
    assert answer["model_status"] == "rag-fallback"
    assert "没有找到足够可靠" in answer["answer"]
    assert len(answer["answer"]) < 160
    assert trace_tool(answer["agent"], "knowledge_search")["observation"]["reference_count"] == 0
    assert answer["agent"]["trace"][-1]["observation"]["action"] == "handoff_recommended"


def test_daily_service_questions_use_llm_answers(
    client: TestClient,
    auth_headers: Callable[[str, str], dict[str, str]],
) -> None:
    user_headers = auth_headers("user", "user123")

    for question in [
        "MFA 验证码收不到，刚换过手机，应该怎么处理？",
        "Outlook 一直离线，收不到邮件怎么排查？",
        "打印机任务卡在队列里，无法打印怎么办？",
    ]:
        answer = ask(client, user_headers, question)

        assert answer["references"], question
        assert answer["llm_used"] is True
        assert answer["model_status"] == "fake-vllm"
        assert answer["risk_level"] in {"low", "medium"}


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
    assert evaluate.json()["total"] == 10
    assert evaluate.json()["pass_rate"] == 1.0

    forbidden = client.get("/api/rag/evaluate", headers=user_headers)
    assert forbidden.status_code == 403, forbidden.text
