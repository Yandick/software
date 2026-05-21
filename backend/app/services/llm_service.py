from __future__ import annotations

import json
import re
from typing import Any

import httpx

from ..config import get_settings


class LLMService:
    """Mandatory Qwen3 digital-employee inference through vLLM."""

    def generate(self, question: str, context: str, enable_thinking: bool | None = None) -> dict[str, Any]:
        settings = get_settings()
        thinking = settings.enable_thinking if enable_thinking is None else enable_thinking
        messages = self._build_messages(question, context, thinking)
        return self._generate_vllm(messages, thinking)

    def extract_issue_draft(self, description: str, rule_draft: dict[str, Any]) -> dict[str, Any]:
        """Extract a structured issue draft with Qwen and merge it with rule fallback."""
        messages = self._build_issue_extract_messages(description, rule_draft)
        result = self._generate_vllm(messages, thinking=False)
        parsed = self._parse_json_object(result["content"])
        return self._normalize_issue_draft(description, rule_draft, parsed, result.get("status", "vllm"))

    def status(self) -> dict[str, Any]:
        settings = get_settings()
        try:
            with httpx.Client(timeout=2) as client:
                resp = client.get(f"{settings.vllm_base_url.rstrip('/')}/models")
                resp.raise_for_status()
        except Exception as exc:
            return {
                "employee_name": "云维",
                "employee_role": "企业运维数字员工",
                "mode": "llm_required",
                "ready": False,
                "vllm_base_url": settings.vllm_base_url,
                "vllm_model_name": settings.vllm_model_name,
                "error": exc.__class__.__name__,
            }
        return {
            "employee_name": "云维",
            "employee_role": "企业运维数字员工",
            "mode": "llm",
            "ready": True,
            "vllm_base_url": settings.vllm_base_url,
            "vllm_model_name": settings.vllm_model_name,
            "model_path": settings.model_path,
            "enable_thinking": settings.enable_thinking,
            "reasoning_request_supported": True,
            "reasoning_parser": "qwen3",
        }

    def _build_messages(self, question: str, context: str, thinking: bool) -> list[dict[str, str]]:
        mode_hint = "/think" if thinking else "/no_think"
        system = (
            "You are Yunwei, an enterprise operations digital employee. "
            "You are not a general chatbot. Your job is to receive IT operations requests, "
            "answer with the private knowledge-base context, guide the user through safe self-service steps, "
            "and create a clear handoff path when a human operator is required. "
            "Use the provided knowledge-base context as your only factual source. "
            "If the context is insufficient, say that the knowledge base is insufficient and ask for the missing fields; "
            "do not invent URLs, phone numbers, commands, account permissions, system names, or policy conclusions. "
            "For account freeze/unfreeze, permission changes, production data, databases, deletion, restart, batch changes, "
            "root/sudo, or any other high-risk operation, provide only controlled process guidance and risk reminders; "
            "the actual operation must be performed through authorized backend workflows and audited human operators. "
            "Always answer in Chinese because the end user speaks Chinese. "
            "Use this exact structure: 1) 结论; 2) 处理步骤; 3) 需要补充的信息; 4) 是否建议转人工; 5) 引用来源. "
            "Be concise, operational, and behave like an on-duty operations employee."
        )
        user = f"{mode_hint}\nKnowledge-base context:\n{context or 'No available context'}\n\nUser question:\n{question}"
        return [{"role": "system", "content": system}, {"role": "user", "content": user}]

    def _build_issue_extract_messages(self, description: str, rule_draft: dict[str, Any]) -> list[dict[str, str]]:
        system = (
            "You are an enterprise IT operations ticket-field extractor. "
            "Extract only facts present in the user's Chinese issue description. "
            "Do not invent account names, phone numbers, URLs, logs, systems, impact scope, or priority. "
            "Return one JSON object only, with no markdown and no explanation. "
            "Allowed category values: account, network, business, database, general. "
            "Allowed priority values: low, medium, high. "
            "Allowed missing_fields values: 联系方式, 影响范围, 截图/附件链接, 错误日志或报错原文."
        )
        schema = {
            "title": "short ticket title, max 60 Chinese chars",
            "description": "original or concise issue description",
            "category": "account|network|business|database|general",
            "priority": "low|medium|high",
            "impact_scope": "affected users/scope if explicitly stated",
            "contact_phone": "phone or contact method if explicitly stated",
            "attachment_url": "URL/file path/share path if explicitly stated",
            "log_excerpt": "error/log lines if explicitly stated",
            "missing_fields": ["missing field labels from the allowed list"],
            "confidence": "number from 0 to 1",
        }
        user = (
            "/no_think\n"
            f"User issue description:\n{description.strip()}\n\n"
            f"Rule fallback draft:\n{json.dumps(rule_draft, ensure_ascii=False)}\n\n"
            f"Required JSON schema:\n{json.dumps(schema, ensure_ascii=False)}"
        )
        return [{"role": "system", "content": system}, {"role": "user", "content": user}]

    def _sampling(self, thinking: bool) -> dict[str, Any]:
        if thinking:
            return {"temperature": 0.6, "top_p": 0.95, "top_k": 20, "min_p": 0, "presence_penalty": 1.1}
        return {"temperature": 0.7, "top_p": 0.8, "top_k": 20, "min_p": 0, "presence_penalty": 1.1}

    def _generate_vllm(self, messages: list[dict[str, str]], thinking: bool) -> dict[str, Any]:
        settings = get_settings()
        payload: dict[str, Any] = {
            "model": settings.vllm_model_name,
            "messages": messages,
            "max_tokens": settings.max_new_tokens,
            **self._sampling(thinking),
            "chat_template_kwargs": {"enable_thinking": thinking},
        }
        try:
            with httpx.Client(timeout=60) as client:
                resp = client.post(f"{settings.vllm_base_url.rstrip('/')}/chat/completions", json=payload)
                resp.raise_for_status()
                data = resp.json()
            msg = data["choices"][0]["message"]
            content = msg.get("content") or ""
            reasoning = msg.get("reasoning_content") or ""
            content, parsed_reasoning = self._strip_think(content)
            if not content.strip():
                raise RuntimeError("empty_llm_response")
            return {
                "ok": True,
                "content": content.strip(),
                "reasoning_content": reasoning or parsed_reasoning,
                "reasoning_enabled": thinking,
                "reasoning_available": bool(reasoning or parsed_reasoning),
                "status": "vllm",
            }
        except Exception as exc:
            raise RuntimeError(f"vllm_required_but_unavailable:{exc.__class__.__name__}") from exc

    def _strip_think(self, text: str) -> tuple[str, str]:
        match = re.search(r"<think>(.*?)</think>", text, flags=re.S)
        if not match:
            return text, ""
        reasoning = match.group(1).strip()
        content = (text[: match.start()] + text[match.end() :]).strip()
        return content, reasoning

    def _parse_json_object(self, text: str) -> dict[str, Any]:
        cleaned = text.strip()
        if cleaned.startswith("```"):
            cleaned = re.sub(r"^```(?:json)?\s*", "", cleaned)
            cleaned = re.sub(r"\s*```$", "", cleaned)
        try:
            data = json.loads(cleaned)
        except json.JSONDecodeError:
            match = re.search(r"\{.*\}", cleaned, flags=re.S)
            if not match:
                raise RuntimeError("issue_extract_json_not_found") from None
            data = json.loads(match.group(0))
        if not isinstance(data, dict):
            raise RuntimeError("issue_extract_json_not_object")
        return data

    def _normalize_issue_draft(
        self,
        description: str,
        rule_draft: dict[str, Any],
        parsed: dict[str, Any],
        status: str,
    ) -> dict[str, Any]:
        categories = {"account", "network", "business", "database", "general"}
        priorities = {"low", "medium", "high"}
        allowed_missing = ["联系方式", "影响范围", "截图/附件链接", "错误日志或报错原文"]

        def text_value(key: str, limit: int = 1000) -> str:
            value = parsed.get(key)
            if value is None or value == "":
                value = rule_draft.get(key, "")
            return str(value).strip()[:limit]

        category = str(parsed.get("category") or rule_draft.get("category") or "general").strip()
        if category not in categories:
            category = str(rule_draft.get("category") or "general")
        priority = str(parsed.get("priority") or rule_draft.get("priority") or "medium").strip()
        if priority not in priorities:
            priority = str(rule_draft.get("priority") or "medium")

        raw_missing = parsed.get("missing_fields")
        missing_fields = [item for item in raw_missing if item in allowed_missing] if isinstance(raw_missing, list) else []

        draft = {
            **rule_draft,
            "title": text_value("title", 60) or rule_draft.get("title") or "在线记录",
            "description": description.strip(),
            "category": category,
            "priority": priority,
            "impact_scope": text_value("impact_scope", 200),
            "contact_phone": text_value("contact_phone", 80),
            "attachment_url": text_value("attachment_url", 500),
            "log_excerpt": text_value("log_excerpt", 1000),
            "missing_fields": missing_fields,
            "confidence": self._clamp_float(parsed.get("confidence"), float(rule_draft.get("confidence", 0.5))),
            "extraction_source": "llm",
            "llm_status": status,
        }
        for field, key in [
            ("联系方式", "contact_phone"),
            ("影响范围", "impact_scope"),
            ("截图/附件链接", "attachment_url"),
            ("错误日志或报错原文", "log_excerpt"),
        ]:
            if not draft.get(key) and field not in draft["missing_fields"]:
                draft["missing_fields"].append(field)
        return draft

    def _clamp_float(self, value: Any, fallback: float) -> float:
        try:
            number = float(value)
        except (TypeError, ValueError):
            number = fallback
        return round(max(0.0, min(1.0, number)), 4)


llm_service = LLMService()
