from __future__ import annotations

from typing import Any

import pytest

from backend.app.services.llm_service import LLMService


def base_rule_draft() -> dict[str, Any]:
    return {
        "attachment_url": "",
        "category": "network",
        "confidence": 0.78,
        "contact_phone": "",
        "description": "VPN 无法连接",
        "extraction_source": "rules",
        "impact_scope": "",
        "log_excerpt": "",
        "missing_fields": ["联系方式", "影响范围", "截图/附件链接", "错误日志或报错原文"],
        "priority": "medium",
        "title": "VPN 无法连接",
    }


def test_extract_issue_draft_parses_fenced_json_and_merges_rule_fallback() -> None:
    service = LLMService()
    service._generate_vllm = lambda messages, thinking: {  # type: ignore[method-assign]
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
        "ok": True,
        "status": "fake-extract",
    }

    draft = service.extract_issue_draft("VPN 无法连接，提示证书过期，影响远程办公，电话 13800138000", base_rule_draft())

    assert draft["extraction_source"] == "llm"
    assert draft["llm_status"] == "fake-extract"
    assert draft["title"] == "VPN 证书过期无法连接"
    assert draft["category"] == "network"
    assert draft["contact_phone"] == "13800138000"
    assert draft["impact_scope"] == "远程办公"
    assert "联系方式" not in draft["missing_fields"]
    assert "影响范围" not in draft["missing_fields"]


def test_extract_issue_draft_rejects_non_json_content() -> None:
    service = LLMService()
    service._generate_vllm = lambda messages, thinking: {"content": "not json", "ok": True, "status": "fake-extract"}  # type: ignore[method-assign]

    with pytest.raises(RuntimeError, match="issue_extract_json_not_found"):
        service.extract_issue_draft("VPN 无法连接", base_rule_draft())
