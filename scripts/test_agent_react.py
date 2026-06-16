#!/usr/bin/env python3
from __future__ import annotations

import os
import sys
import tempfile
from pathlib import Path
from typing import Any


def main() -> None:
    repo_root = Path(__file__).resolve().parents[1]
    sys.path.insert(0, str(repo_root))
    with tempfile.TemporaryDirectory(prefix="ops-agent-react-") as tmpdir:
        db_path = Path(tmpdir) / "agent_react.db"
        os.environ["OPS_DATABASE_URL"] = f"sqlite:///{db_path}"

        from fastapi.testclient import TestClient

        from backend.app.config import get_settings
        from backend.app.database import _hash_password, connect, init_db, utc_now
        from backend.app.services.llm_service import LLMService, llm_service
        from backend.app.services import issues_service
        import backend.app.main as app_main

        get_settings.cache_clear()
        init_db()
        issues_service.UPLOAD_DIR = Path(tmpdir) / "uploads"
        issues_service.UPLOAD_DIR.mkdir(parents=True, exist_ok=True)
        with connect() as conn:
            conn.execute(
                "insert into users(username,password_hash,real_name,role,department,created_at) values(?,?,?,?,?,?)",
                ("user2", _hash_password("user2123"), "普通用户二", "user", "业务部门", utc_now()),
            )

        # Unit-check the LLM JSON extractor without hitting a real vLLM server.
        extractor = LLMService()
        extractor._generate_vllm = lambda messages, thinking: {  # type: ignore[method-assign]
            "ok": True,
            "content": """
            ```json
            {
              "title": "VPN 证书过期无法连接",
              "description": "VPN 无法连接，提示证书过期，影响远程办公，电话 13800138000",
              "category": "network",
              "priority": "medium",
              "impact_scope": "远程办公",
              "contact_phone": "13800138000",
              "attachment_url": "",
              "log_excerpt": "",
              "missing_fields": ["截图/附件链接", "错误日志或报错原文"],
              "confidence": 0.91
            }
            ```
            """,
            "status": "fake-extract",
        }
        rule_draft = {
            "title": "VPN 无法连接",
            "description": "VPN 无法连接",
            "category": "network",
            "priority": "medium",
            "impact_scope": "",
            "contact_phone": "",
            "attachment_url": "",
            "log_excerpt": "",
            "missing_fields": ["联系方式", "影响范围", "截图/附件链接", "错误日志或报错原文"],
            "confidence": 0.78,
            "extraction_source": "rules",
        }
        extracted = extractor.extract_issue_draft(
            "VPN 无法连接，提示证书过期，影响远程办公，电话 13800138000",
            rule_draft,
        )
        assert extracted["extraction_source"] == "llm"
        assert extracted["category"] == "network"
        assert extracted["contact_phone"] == "13800138000"
        assert extracted["impact_scope"] == "远程办公"
        assert "联系方式" not in extracted["missing_fields"]

        def fake_generate(question: str, context: str, enable_thinking: bool | None = None) -> dict[str, object]:
            assert "VPN" in question or "vpn" in question.lower()
            assert "Knowledge" not in question  # Ensure the app passes the clean/effective user question.
            return {
                "ok": True,
                "content": "测试回答：已检索知识库，建议先检查 VPN 证书和客户端版本；无法解决请转人工。",
                "reasoning_content": "",
                "reasoning_enabled": bool(enable_thinking),
                "reasoning_available": False,
                "status": "fake-vllm",
            }

        def fake_extract_issue_draft(description: str, rule_draft: dict[str, Any]) -> dict[str, Any]:
            assert "VPN" in description or "vpn" in description.lower()
            return {
                **rule_draft,
                "title": "VPN 证书过期无法连接",
                "category": "network",
                "priority": "medium",
                "impact_scope": "远程办公",
                "contact_phone": "13800138000",
                "attachment_url": "",
                "log_excerpt": "",
                "missing_fields": ["截图/附件链接", "错误日志或报错原文"],
                "confidence": 0.91,
                "extraction_source": "llm",
                "llm_status": "fake-extract",
            }

        llm_service.generate = fake_generate
        llm_service.extract_issue_draft = fake_extract_issue_draft
        client = TestClient(app_main.app)

        login = client.post("/api/auth/login", json={"username": "user", "password": "user123"})
        assert login.status_code == 200, login.text
        token = login.json()["access_token"]
        headers = {"Authorization": f"Bearer {token}"}
        user2_login = client.post("/api/auth/login", json={"username": "user2", "password": "user2123"})
        assert user2_login.status_code == 200, user2_login.text
        user2_headers = {"Authorization": f"Bearer {user2_login.json()['access_token']}"}

        status = client.get("/api/agent/status", headers=headers)
        assert status.status_code == 200, status.text
        status_body = status.json()
        assert status_body["mode"] == "controlled_react_prototype"
        assert set(status_body["tools"]) == {"handoff_script", "issue_draft", "knowledge_search"}

        question = "VPN 无法连接，提示证书过期，影响远程办公，电话 13800138000"
        ask = client.post(
            "/api/qa/ask",
            json={"question": question, "enable_thinking": False},
            headers=headers,
        )
        assert ask.status_code == 200, ask.text
        body = ask.json()
        assert body["llm_used"] is True
        assert body["model_status"] == "fake-vllm"
        assert body["agent"]["mode"] == "controlled_react_prototype"
        assert body["agent"]["decision"]["action"] in {"self_service_first", "clarify_then_self_service"}
        assert body["agent"]["issue_draft"]["category"] == "network"
        assert body["agent"]["issue_draft"]["extraction_source"] == "llm"
        assert body["issue_draft"]["extraction_source"] == "llm"
        assert body["issue_draft"]["contact_phone"] == "13800138000"
        assert "联系方式" not in body["missing_fields"]

        trace = body["agent"]["trace"]
        phases = [step["phase"] for step in trace]
        tools = [step["tool"] for step in trace]
        assert phases == ["Reason", "Act", "Act", "Act", "Final"], phases
        assert "knowledge_search" in tools
        assert "issue_draft" in tools
        assert "handoff_script" in tools
        assert trace[1]["observation"]["reference_count"] >= 1
        assert trace[2]["observation"]["extraction_source"] == "llm"

        draft_resp = client.post("/api/issues/draft", json={"description": question}, headers=headers)
        assert draft_resp.status_code == 200, draft_resp.text
        draft_body = draft_resp.json()
        assert draft_body["extraction_source"] == "llm"
        assert draft_body["contact_phone"] == "13800138000"

        issue = client.post("/api/issues", json=body["issue_draft"], headers=headers)
        assert issue.status_code == 200, issue.text
        issue_id = issue.json()["id"]
        issues = client.get("/api/issues", headers=headers)
        assert issues.status_code == 200, issues.text
        assert any(item["id"] == issue_id for item in issues.json())

        conversation_id = body["conversation_id"]
        conversations = client.get("/api/qa/conversations", headers=headers)
        assert conversations.status_code == 200, conversations.text
        assert any(item["id"] == conversation_id for item in conversations.json())

        detail = client.get(f"/api/qa/conversations/{conversation_id}", headers=headers)
        assert detail.status_code == 200, detail.text
        messages = detail.json()["messages"]
        assert len(messages) == 2, messages
        assistant_msg = messages[-1]
        assert assistant_msg["role"] == "assistant"
        metadata = assistant_msg["metadata"]
        assert metadata["agent"]["mode"] == "controlled_react_prototype"
        assert metadata["agent"]["trace"][1]["tool"] == "knowledge_search"
        assert metadata["issue_draft"]["category"] == "network"
        assert metadata["issue_draft"]["extraction_source"] == "llm"

        upload = client.post(
            "/api/issues/attachments",
            files={"file": ("vpn-error.txt", b"certificate expired", "text/plain")},
            headers=headers,
        )
        assert upload.status_code == 200, upload.text
        attachment_url = upload.json()["url"]
        other_download = client.get(attachment_url, headers=user2_headers)
        assert other_download.status_code == 403, other_download.text

        admin_login = client.post("/api/auth/login", json={"username": "admin", "password": "admin123"})
        assert admin_login.status_code == 200, admin_login.text
        admin_headers = {"Authorization": f"Bearer {admin_login.json()['access_token']}"}

        ops_login = client.post("/api/auth/login", json={"username": "ops", "password": "ops123"})
        assert ops_login.status_code == 200, ops_login.text
        ops_headers = {"Authorization": f"Bearer {ops_login.json()['access_token']}"}
        auditor_login = client.post("/api/auth/login", json={"username": "auditor", "password": "audit123"})
        assert auditor_login.status_code == 200, auditor_login.text
        auditor_headers = {"Authorization": f"Bearer {auditor_login.json()['access_token']}"}

        sensitive_check = client.post(
            "/api/knowledge/sensitive-check",
            json={"title": "VPN 案例", "content": "用户电话 13800138000，password=abc123", "tags": "VPN,案例"},
            headers=admin_headers,
        )
        assert sensitive_check.status_code == 200, sensitive_check.text
        sensitive_body = sensitive_check.json()
        assert sensitive_body["blocking"] is True
        assert "[手机号已脱敏]" in sensitive_body["redacted"]["content"]

        sensitive_publish = client.post(
            "/api/knowledge",
            json={"title": "敏感发布测试", "content": "用户电话 13800138000", "status": "published"},
            headers=admin_headers,
        )
        assert sensitive_publish.status_code == 400, sensitive_publish.text

        cross_write = client.post(
            "/api/qa/ask",
            json={"question": question, "conversation_id": conversation_id},
            headers=ops_headers,
        )
        assert cross_write.status_code == 403, cross_write.text

        ops_demo = client.post("/api/demo/session", headers=ops_headers)
        assert ops_demo.status_code == 403, ops_demo.text

        knowledge = client.post(
            "/api/knowledge",
            json={"title": "ops 发布测试", "content": "测试内容", "status": "published"},
            headers=ops_headers,
        )
        assert knowledge.status_code == 200, knowledge.text
        assert knowledge.json()["status"] == "pending_review"
        publish_by_ops = client.post(f"/api/knowledge/{knowledge.json()['id']}/status", json={"status": "published"}, headers=ops_headers)
        assert publish_by_ops.status_code == 403, publish_by_ops.text
        sensitive_candidate = client.post(
            "/api/knowledge",
            json={"title": "待脱敏候选", "content": "用户电话 13800138000", "status": "pending_review"},
            headers=ops_headers,
        )
        assert sensitive_candidate.status_code == 200, sensitive_candidate.text
        blocked_publish = client.post(
            f"/api/knowledge/{sensitive_candidate.json()['id']}/status",
            json={"status": "published"},
            headers=admin_headers,
        )
        assert blocked_publish.status_code == 400, blocked_publish.text
        ops_update_candidate = client.put(
            f"/api/knowledge/{knowledge.json()['id']}",
            json={"title": "ops 候选更新", "content": "更新候选内容", "status": "published"},
            headers=ops_headers,
        )
        assert ops_update_candidate.status_code == 200, ops_update_candidate.text
        assert ops_update_candidate.json()["status"] == "pending_review"
        published_rows = client.get("/api/knowledge", params={"status": "published"}, headers=ops_headers)
        assert published_rows.status_code == 200, published_rows.text
        published_id = published_rows.json()[0]["id"]
        ops_update_published = client.put(
            f"/api/knowledge/{published_id}",
            json={"title": "ops 不应改已发布", "content": "测试内容", "status": "pending_review"},
            headers=ops_headers,
        )
        assert ops_update_published.status_code == 403, ops_update_published.text

        hidden = client.get("/api/knowledge", params={"status": "pending_review"}, headers=headers)
        assert hidden.status_code == 200, hidden.text
        assert all(item["status"] == "published" for item in hidden.json())

        missing_handle = client.post("/api/issues/99999/handle", json={"solution": "no such issue"}, headers=ops_headers)
        assert missing_handle.status_code == 404, missing_handle.text

        hijack_issue = client.post("/api/issues", json={**body["issue_draft"], "attachment_url": attachment_url}, headers=user2_headers)
        assert hijack_issue.status_code == 403, hijack_issue.text
        own_issue = client.post("/api/issues", json={**body["issue_draft"], "attachment_url": attachment_url}, headers=headers)
        assert own_issue.status_code == 200, own_issue.text
        bound_download = client.get(attachment_url, headers=headers)
        assert bound_download.status_code == 200, bound_download.text
        other_bound_download = client.get(attachment_url, headers=user2_headers)
        assert other_bound_download.status_code == 403, other_bound_download.text

        demo = client.post("/api/demo/session", headers=admin_headers)
        assert demo.status_code == 200, demo.text
        demo_body = demo.json()
        demo_id = demo_body["id"]
        for _ in range(len(demo_body["steps"])):
            step = client.post(f"/api/demo/session/{demo_id}/step", headers=admin_headers)
            assert step.status_code == 200, step.text
            demo_body = step.json()
        assert demo_body["status"] == "finished"
        assert demo_body["agent_window"]["trace"][1]["tool"] == "knowledge_search"
        assert demo_body["ops_window"]["issue"]["status"] == "closed"
        assert demo_body["admin_window"]["knowledge"]["status"] == "published"
        assert demo_body["account_window"]["approval"]["status"] == "approved"
        assert demo_body["account_window"]["account"]["status"] == "active"
        assert demo_body["fallback_conversation_id"]
        assert demo_body["admin_window"]["audit"]
        demo_issue_id = demo_body["ops_window"]["issue"]["id"]
        demo_conversation_id = demo_body["conversation_id"]
        user_demo_issues = client.get("/api/issues", headers=headers)
        assert user_demo_issues.status_code == 200, user_demo_issues.text
        assert any(item["id"] == demo_issue_id for item in user_demo_issues.json())
        user_demo_conversations = client.get("/api/qa/conversations", headers=headers)
        assert user_demo_conversations.status_code == 200, user_demo_conversations.text
        assert any(item["id"] == demo_conversation_id for item in user_demo_conversations.json())

        user_stats = client.get("/api/audit/stats", headers=headers)
        assert user_stats.status_code == 200, user_stats.text
        assert "accounts" not in user_stats.json()
        auditor_stats = client.get("/api/audit/stats", headers=auditor_headers)
        assert auditor_stats.status_code == 200, auditor_stats.text
        assert "accounts" in auditor_stats.json()
        audit_export = client.get("/api/audit/export", headers=auditor_headers)
        assert audit_export.status_code == 200, audit_export.text
        assert "log_type,id,event_type" in audit_export.json()["content"]

        print(
            {
                "ok": True,
                "conversation_id": conversation_id,
                "issue_id": issue_id,
                "demo_id": demo_id,
                "agent_tools": body["agent"]["tools_used"],
                "trace_steps": len(trace),
                "extract_source": body["issue_draft"]["extraction_source"],
                "db": str(db_path),
            }
        )


if __name__ == "__main__":
    main()
