from __future__ import annotations

import json
import re
from typing import Any

import httpx

from ..config import get_settings


class LLMService:
    """Mandatory Qwen3 digital-employee inference through vLLM."""

    def generate(
        self,
        question: str,
        context: str,
        enable_thinking: bool | None = None,
        answer_plan: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        settings = get_settings()
        thinking = settings.enable_thinking if enable_thinking is None else enable_thinking
        messages = self._build_messages(question, context, thinking, answer_plan)
        return self._generate_vllm(messages, thinking)

    def extract_issue_draft(self, description: str, rule_draft: dict[str, Any]) -> dict[str, Any]:
        """Extract a structured issue draft with Qwen and merge it with rule fallback."""
        messages = self._build_issue_extract_messages(description, rule_draft)
        result = self._generate_vllm(messages, thinking=False)
        parsed = self._parse_json_object(result["content"])
        return self._normalize_issue_draft(description, rule_draft, parsed, result.get("status", "vllm"))

    def generate_agent_json(
        self,
        *,
        agent_name: str,
        prompt: str,
        task: str,
        state: dict[str, Any],
        schema_hint: dict[str, Any],
    ) -> dict[str, Any]:
        """Ask one subagent for a structured review.

        The result is advisory metadata. Callers must keep deterministic safety
        gates as the final authority for writes and high-risk operations.
        """
        user = (
            "/no_think\n"
            f"Agent name: {agent_name}\n"
            f"Task:\n{task}\n\n"
            "Workflow state JSON:\n"
            f"{json.dumps(state, ensure_ascii=False, default=str)[:12000]}\n\n"
            "Return one JSON object only. Follow this schema hint:\n"
            f"{json.dumps(schema_hint, ensure_ascii=False, default=str)}"
        )
        messages = [
            {"role": "system", "content": prompt},
            {"role": "user", "content": user},
        ]
        result = self._generate_vllm(messages, thinking=False, timeout=get_settings().agent_llm_timeout_seconds)
        parsed = self._parse_json_object(result["content"])
        return {
            "agent": agent_name,
            "ok": True,
            "parsed": parsed,
            "reasoning_available": result.get("reasoning_available", False),
            "status": result.get("status", "vllm"),
        }

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

    def _build_messages(
        self,
        question: str,
        context: str,
        thinking: bool,
        answer_plan: dict[str, Any] | None = None,
    ) -> list[dict[str, str]]:
        mode_hint = "/think" if thinking else "/no_think"
        plan = answer_plan or {
            "mode": "concise_resolution",
            "purpose": "Answer directly using the strongest evidence.",
            "sections": ["答案", "操作要点"],
            "style": "natural, concise, evidence-grounded",
        }
        system = (
            "You are Yunwei, an enterprise operations digital employee. "
            "You are not a general chatbot. Your job is to receive IT operations requests, "
            "answer with the private knowledge-base context, guide the user through safe self-service steps, "
            "and create a clear handoff path when a human operator is required. "
            "Use the provided knowledge-base context as your only factual source. "
            "Knowledge references are ranked by relevance. Treat reference [1] as the primary evidence, "
            "especially when it is marked PRIMARY_MATCH or has a high score. If [1] is a case, runbook, FAQ, "
            "or policy that contains concrete cause, handling steps, recovery method, or cautions, reuse those "
            "specific details before giving generic troubleshooting advice. If a newer or higher-ranked case "
            "conflicts with a generic FAQ, prefer the higher-ranked case. "
            "When the primary reference includes sections such as 处理步骤, 恢复方式, or 注意事项, include those "
            "section details in the final answer. For operator-only actions such as rollback, cache cleanup, "
            "restart, permission change, or production configuration change, do not tell the end user to execute "
            "them directly; say they should be performed or verified by authorized operations staff. "
            "End users may clear only browser/site cache, retry login, switch browser, collect screenshots/log text, "
            "and submit an online record. Gateway cache cleanup, version rollback, release changes, service restarts, "
            "and server-side log inspection must be placed under operations-staff actions. "
            "If the context is insufficient, say that the knowledge base is insufficient and ask for the missing fields; "
            "do not invent URLs, phone numbers, commands, account permissions, system names, or policy conclusions. "
            "For account freeze/unfreeze, permission changes, production data, databases, deletion, restart, batch changes, "
            "root/sudo, or any other high-risk operation, provide only controlled process guidance and risk reminders; "
            "the actual operation must be performed through authorized backend workflows and audited human operators. "
            "Always answer in Chinese because the end user speaks Chinese. "
            "Do not use a single fixed template for every answer. Follow the provided Answer plan. "
            "The Answer plan is internal instruction metadata; never quote it, summarize it, expose JSON, "
            "or mention field names such as mode, purpose, sections, style, or avoid. "
            "Use only the sections requested by the plan, rename them naturally in Chinese when appropriate, "
            "and omit irrelevant sections. Avoid stock phrases and generic endings when the plan does not ask for them. "
            "Do not write a 引用, 引用来源, 参考来源, or sources section in the answer body; the frontend renders "
            "retrieved references separately. "
            "Be concise, operational, and behave like an on-duty operations employee."
        )
        user = (
            f"{mode_hint}\n"
            "Answer plan JSON:\n"
            f"{json.dumps(plan, ensure_ascii=False, default=str)}\n\n"
            "Answer grounding requirements:\n"
            "- The Answer plan JSON is not user-facing content. Do not print, quote, or paraphrase the JSON itself.\n"
            "- First decide from the ranked knowledge whether the issue matches reference [1].\n"
            "- If it matches, base the conclusion and steps mainly on [1], but do not write inline citations or 引用来源.\n"
            "- If [1] has 处理步骤 or 恢复方式, include those concrete steps in the answer; mark operator-only steps as 运维执行.\n"
            "- In 用户可自助, never include gateway cache cleanup, rollback, release changes, server-side log inspection, restart, or permission changes.\n"
            "- Do not output knowledge_id, 知识_id, source ids, or a reference list in the answer body.\n"
            "- Follow the plan's mode, purpose, sections, and style. Do not fall back to the old universal five-section format.\n"
            "- If the plan says diagnostic_judgement, lead with judgement and evidence instead of a long checklist.\n"
            "- If the plan says self_service_steps, separate user-safe actions from operations-staff actions.\n"
            "- If the plan says knowledge_explanation, explain mechanism and applicability instead of drafting a ticket.\n"
            "- Use lower-ranked references only as supplements; do not let generic references override the primary match.\n\n"
            f"Knowledge-base context:\n{context or 'No available context'}\n\n"
            f"User question:\n{question}"
        )
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

    def _generate_vllm(self, messages: list[dict[str, str]], thinking: bool, timeout: int = 60) -> dict[str, Any]:
        settings = get_settings()
        payload: dict[str, Any] = {
            "model": settings.vllm_model_name,
            "messages": messages,
            "max_tokens": settings.max_new_tokens,
            **self._sampling(thinking),
            "chat_template_kwargs": {"enable_thinking": thinking},
        }
        try:
            with httpx.Client(timeout=timeout) as client:
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
