from __future__ import annotations

import re
from typing import Any

import httpx

from ..config import get_settings


class LLMService:
    """Qwen3 inference wrapper: prefer vLLM OpenAI API, fallback to Transformers."""

    def __init__(self) -> None:
        self._tokenizer = None
        self._model = None
        self._transformers_error: str | None = None

    def generate(self, question: str, context: str, enable_thinking: bool | None = None) -> dict[str, Any]:
        settings = get_settings()
        thinking = settings.enable_thinking if enable_thinking is None else enable_thinking
        messages = self._build_messages(question, context, thinking)
        backend = settings.inference_backend.lower()
        if backend in {"auto", "vllm"}:
            result = self._generate_vllm(messages, thinking)
            if result["ok"] or backend == "vllm":
                return result
        if backend in {"auto", "transformers"}:
            result = self._generate_transformers(messages, thinking)
            if result["ok"] or backend == "transformers":
                return result
        return {"ok": False, "content": "", "reasoning_content": "", "status": "retrieval_only"}

    def _build_messages(self, question: str, context: str, thinking: bool) -> list[dict[str, str]]:
        mode_hint = "/think" if thinking else "/no_think"
        system = (
            "你是企业运维数字员工。必须只基于给定知识库上下文回答；"
            "没有依据时明确建议转人工，不能编造网址、电话、命令或权限结论。"
            "涉及账号冻结/解冻、权限变更、生产数据、数据库、删除、重启等高风险操作时，"
            "只能给出受控流程和风险提示，实际操作必须由后台接口和有权限人员执行。"
            "输出格式：先给结论，再给步骤，最后列出引用来源标题。"
        )
        user = f"{mode_hint}\n知识库上下文：\n{context or '无可用上下文'}\n\n用户问题：{question}"
        return [{"role": "system", "content": system}, {"role": "user", "content": user}]

    def _sampling(self, thinking: bool) -> dict[str, Any]:
        settings = get_settings()
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
            return {
                "ok": True,
                "content": content.strip(),
                "reasoning_content": reasoning or parsed_reasoning,
                "status": "vllm",
            }
        except Exception as exc:
            return {"ok": False, "content": "", "reasoning_content": "", "status": f"vllm_unavailable:{exc.__class__.__name__}"}

    def _generate_transformers(self, messages: list[dict[str, str]], thinking: bool) -> dict[str, Any]:
        settings = get_settings()
        try:
            self._ensure_transformers()
            if not self._tokenizer or not self._model:
                return {"ok": False, "content": "", "reasoning_content": "", "status": self._transformers_error or "transformers_unavailable"}
            text = self._tokenizer.apply_chat_template(
                messages,
                tokenize=False,
                add_generation_prompt=True,
                enable_thinking=thinking,
            )
            inputs = self._tokenizer([text], return_tensors="pt").to(self._model.device)
            outputs = self._model.generate(
                **inputs,
                max_new_tokens=settings.max_new_tokens,
                do_sample=True,
                temperature=0.6 if thinking else 0.7,
                top_p=0.95 if thinking else 0.8,
                top_k=20,
                pad_token_id=self._tokenizer.eos_token_id,
            )
            output_ids = outputs[0][len(inputs.input_ids[0]):].tolist()
            decoded = self._tokenizer.decode(output_ids, skip_special_tokens=True)
            content, reasoning = self._strip_think(decoded)
            return {"ok": True, "content": content.strip(), "reasoning_content": reasoning, "status": "transformers"}
        except Exception as exc:
            return {"ok": False, "content": "", "reasoning_content": "", "status": f"transformers_error:{exc.__class__.__name__}"}

    def _ensure_transformers(self) -> None:
        if self._model and self._tokenizer:
            return
        if self._transformers_error:
            return
        settings = get_settings()
        try:
            from transformers import AutoModelForCausalLM, AutoTokenizer

            self._tokenizer = AutoTokenizer.from_pretrained(settings.model_path, trust_remote_code=True)
            self._model = AutoModelForCausalLM.from_pretrained(
                settings.model_path,
                torch_dtype="auto",
                device_map="auto",
                trust_remote_code=True,
            )
        except Exception as exc:
            self._transformers_error = f"transformers_unavailable:{exc.__class__.__name__}"

    def _strip_think(self, text: str) -> tuple[str, str]:
        match = re.search(r"<think>(.*?)</think>", text, flags=re.S)
        if not match:
            return text, ""
        reasoning = match.group(1).strip()
        content = (text[: match.start()] + text[match.end() :]).strip()
        return content, reasoning


llm_service = LLMService()
