from __future__ import annotations

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


llm_service = LLMService()
