from __future__ import annotations

import importlib
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable, Protocol

from ..config import get_settings
from ..schemas import KnowledgeCreate
from .llm_service import llm_service


class SearchService(Protocol):
    def search(self, question: str, limit: int = 4) -> Any:
        ...


DraftBuilder = Callable[[str], dict[str, Any]]


@dataclass
class AgentStep:
    agent: str
    phase: str
    tool: str
    thought: str
    observation: dict[str, Any] = field(default_factory=dict)


class AgentService:
    """Single-Qwen multi-agent orchestrator.

    The deployment still uses one local Qwen/vLLM endpoint. "Sub agents" here
    are bounded roles with different prompts, tool permissions, trace entries,
    and deterministic safety gates. This keeps the agentic workflow auditable
    while allowing the same base model to act as an ops employee, risk guard,
    knowledge curator, and evaluator.
    """

    MODE = "single_qwen_multi_agent_orchestrator"
    PROMPT_ROOT = Path(__file__).resolve().parents[1] / "agents"
    SAFE_TOOLS = {
        "answer_evaluator",
        "handoff_script",
        "intent_route",
        "issue_draft",
        "knowledge_autonomous_ingest",
        "knowledge_duplicate_check",
        "knowledge_search",
        "risk_classify",
        "supervisor_route",
    }
    AGENT_DEFINITIONS = [
        {
            "name": "intent_router",
            "role": "Scope, intent, and fallback router",
            "prompt_dir": "intent_router",
            "tools": ["intent_route"],
            "writes": False,
        },
        {
            "name": "supervisor",
            "role": "Router / coordinator",
            "prompt_dir": "supervisor",
            "tools": ["supervisor_route"],
            "writes": False,
        },
        {
            "name": "risk_guardian",
            "role": "Sensitive and controlled-operation gate",
            "prompt_dir": "risk_guardian",
            "tools": ["risk_classify"],
            "writes": False,
        },
        {
            "name": "ops_employee",
            "role": "RAG answer, issue draft, and handoff",
            "prompt_dir": "ops_employee",
            "tools": ["knowledge_search", "issue_draft", "handoff_script"],
            "writes": False,
        },
        {
            "name": "knowledge_curator",
            "role": "Autonomous knowledge dedupe, skip, merge, and candidate creation",
            "prompt_dir": "knowledge_curator",
            "tools": ["knowledge_duplicate_check", "knowledge_autonomous_ingest"],
            "writes": "knowledge table through RBAC-checked backend tools only",
        },
        {
            "name": "evaluator",
            "role": "Final guardrail and answer-readiness check",
            "prompt_dir": "evaluator",
            "tools": ["answer_evaluator"],
            "writes": False,
        },
    ]

    def __init__(self) -> None:
        self.agent_prompts = self._load_agent_prompts()

    def agents(self) -> list[dict[str, Any]]:
        cards = []
        for item in self.AGENT_DEFINITIONS:
            prompt_path = self._prompt_path(str(item["prompt_dir"]))
            cards.append(
                {
                    "name": item["name"],
                    "role": item["role"],
                    "prompt_path": str(prompt_path.relative_to(Path(__file__).resolve().parents[3])),
                    "prompt_loaded": bool(self.agent_prompts.get(str(item["name"]), "").strip()),
                    "tools": item["tools"],
                    "writes": item["writes"],
                }
            )
        return cards

    def _prompt_path(self, prompt_dir: str) -> Path:
        return self.PROMPT_ROOT / prompt_dir / "prompt.md"

    def _load_agent_prompts(self) -> dict[str, str]:
        prompts: dict[str, str] = {}
        for item in self.AGENT_DEFINITIONS:
            path = self._prompt_path(str(item["prompt_dir"]))
            prompts[str(item["name"])] = path.read_text(encoding="utf-8")
        return prompts

    def _agent_prompt_meta(self, agent_name: str) -> dict[str, Any]:
        for item in self.AGENT_DEFINITIONS:
            if item["name"] != agent_name:
                continue
            prompt_path = self._prompt_path(str(item["prompt_dir"]))
            return {
                "prompt_loaded": bool(self.agent_prompts.get(agent_name, "").strip()),
                "prompt_path": str(prompt_path.relative_to(Path(__file__).resolve().parents[3])),
            }
        return {"prompt_loaded": False, "prompt_path": ""}

    def status(self) -> dict[str, Any]:
        error = ""
        try:
            importlib.import_module("qwen_agent")
            available = True
        except Exception as exc:
            available = False
            error = exc.__class__.__name__
        settings = get_settings()
        return {
            "agents": self.agents(),
            "agent_llm_enabled": settings.enable_agent_llm,
            "agent_llm_parallelism": settings.agent_llm_parallelism,
            "agent_llm_timeout_seconds": settings.agent_llm_timeout_seconds,
            "base_model": settings.vllm_model_name,
            "mode": self.MODE,
            "qwen_agent_available": available,
            "qwen_agent_error": error,
            "single_model_deployment": True,
            "tools": sorted(self.SAFE_TOOLS),
            "safety_boundary": (
                "one local Qwen model; role-specific orchestration; only backend tools may read/write data; "
                "account, production, and knowledge writes remain RBAC/audit controlled"
            ),
        }

    def run(
        self,
        question: str,
        rag_service: SearchService,
        draft_builder: DraftBuilder,
        retrieval: Any | None = None,
        draft: dict[str, Any] | None = None,
        intent_route: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        trace: list[AgentStep] = []

        retrieval = retrieval or rag_service.search(question, limit=4)
        references = self._references(retrieval)
        draft = draft or draft_builder(question)
        intent_route = intent_route or {
            "kind": "ops_support",
            "intent": draft.get("category", "general"),
            "intent_label": "运维咨询",
            "should_rag": True,
            "should_handoff": False,
            "confidence": 0.0,
            "risk_level": "low",
            "reason": "agent_service_default_route",
            "source": "rules",
        }

        trace.append(
            AgentStep(
                agent="intent_router",
                phase="Route",
                tool="intent_route",
                thought="Classify scope and decide whether the request may enter RAG and the ops workflow.",
                observation=intent_route,
            )
        )

        supervisor = self._supervisor_route(question, retrieval, draft, references)
        trace.append(
            AgentStep(
                agent="supervisor",
                phase="Route",
                tool="supervisor_route",
                thought="Classify the request and select the bounded specialist agents.",
                observation=supervisor,
            )
        )

        risk = self._risk_gate(retrieval, draft, references)
        trace.append(
            AgentStep(
                agent="risk_guardian",
                phase="Guard",
                tool="risk_classify",
                thought="Classify controlled-operation risk and evidence sufficiency.",
                observation=risk,
            )
        )

        trace.append(
            AgentStep(
                agent="ops_employee",
                phase="Act",
                tool="knowledge_search",
                thought="Retrieve published private knowledge references for grounded support.",
                observation={
                    "confidence": round(float(retrieval.confidence), 4),
                    "high_risk": bool(retrieval.high_risk),
                    "query_terms": getattr(retrieval, "query_terms", []),
                    "reference_count": len(references),
                    "references": references,
                    "strategy": getattr(retrieval, "strategy", "unknown"),
                },
            )
        )

        trace.append(
            AgentStep(
                agent="ops_employee",
                phase="Act",
                tool="issue_draft",
                thought="Extract a ticket draft without creating a real issue.",
                observation={
                    "category": draft.get("category", "general"),
                    "extraction_source": draft.get("extraction_source", "rules"),
                    "priority": draft.get("priority", "medium"),
                    "missing_fields": draft.get("missing_fields", []),
                    "title": draft.get("title", ""),
                },
            )
        )

        handoff_script = self._build_handoff_script(draft, retrieval.high_risk, bool(references))
        trace.append(
            AgentStep(
                agent="ops_employee",
                phase="Act",
                tool="handoff_script",
                thought="Prepare a handoff script for user and operator continuity.",
                observation={"script": handoff_script},
            )
        )

        curator = self._knowledge_curator_plan(question, retrieval, draft, references)
        trace.append(
            AgentStep(
                agent="knowledge_curator",
                phase="Curate",
                tool="knowledge_duplicate_check",
                thought="Decide whether this interaction should become reusable knowledge and which dedupe path applies.",
                observation=curator,
            )
        )

        evaluator = self._evaluate_answer_readiness(retrieval, references, draft, risk, curator)
        trace.append(
            AgentStep(
                agent="evaluator",
                phase="Evaluate",
                tool="answer_evaluator",
                thought="Check answer readiness, fallback requirements, and audit signals.",
                observation=evaluator,
            )
        )

        decision = self._final_decision(risk, references, draft)
        trace.append(
            AgentStep(
                agent="supervisor",
                phase="Final",
                tool="supervisor_route",
                thought="Combine specialist outputs into the final user-facing action decision.",
                observation=decision,
            )
        )
        llm_reviews = self._llm_agent_reviews(
            task="ops_support_workflow",
            state={
                "question": question,
                "supervisor": supervisor,
                "risk": risk,
                "retrieval": {
                    "confidence": round(float(retrieval.confidence), 4),
                    "high_risk": bool(retrieval.high_risk),
                    "query_terms": getattr(retrieval, "query_terms", []),
                    "strategy": getattr(retrieval, "strategy", "unknown"),
                },
                "references": references,
                "issue_draft": draft,
                "knowledge_curator": curator,
                "evaluator": evaluator,
                "decision": decision,
            },
        )

        return {
            "agents": self.agents(),
            "base_model": get_settings().vllm_model_name,
            "llm_reviews": llm_reviews,
            "mode": self.MODE,
            "tools_used": [step.tool for step in trace if step.tool in self.SAFE_TOOLS],
            "trace": [self._step_to_dict(step) for step in trace],
            "supervisor": supervisor,
            "risk": risk,
            "ops_employee": {
                "issue_draft": draft,
                "handoff_script": handoff_script,
                "reference_count": len(references),
            },
            "knowledge_curator": curator,
            "evaluator": evaluator,
            "issue_draft": draft,
            "handoff_script": handoff_script,
            "decision": decision,
            "qwen_agent_status": self.status(),
        }

    def curate_knowledge(self, data: KnowledgeCreate, user: dict[str, Any]) -> dict[str, Any]:
        from .knowledge_service import autonomous_ingest_knowledge

        result = autonomous_ingest_knowledge(data, user)
        llm_reviews = self._llm_agent_reviews(
            task="knowledge_curation_workflow",
            state={
                "candidate": data.model_dump(),
                "user_role": user.get("role"),
                "curation_result": {
                    "action": result.get("action"),
                    "duplicate_check": result.get("duplicate_check", {}),
                    "item": result.get("item"),
                    "message": result.get("message", ""),
                    "novel_units": result.get("novel_units", []),
                    "redacted": result.get("redacted", False),
                    "sensitive_check": result.get("sensitive_check", {}),
                },
            },
            agent_names=["supervisor", "risk_guardian", "knowledge_curator", "evaluator"],
        )
        agent_trace = [
            self._step_to_dict(
                AgentStep(
                    agent="supervisor",
                    phase="Route",
                    tool="supervisor_route",
                    thought="Route the request as a knowledge curation task.",
                    observation={"route": "knowledge_curation", "selected_agents": ["risk_guardian", "knowledge_curator", "evaluator"]},
                )
            ),
            self._step_to_dict(
                AgentStep(
                    agent="risk_guardian",
                    phase="Guard",
                    tool="risk_classify",
                    thought="Reuse sensitive-data checks and publication constraints from the knowledge service.",
                    observation=result.get("sensitive_check", {}),
                )
            ),
            self._step_to_dict(
                AgentStep(
                    agent="knowledge_curator",
                    phase="Curate",
                    tool="knowledge_autonomous_ingest",
                    thought="Run controlled redaction, duplicate handling, skipping, merging, or candidate creation.",
                    observation={
                        "action": result.get("action"),
                        "duplicate_check": result.get("duplicate_check", {}),
                        "item": result.get("item"),
                        "novel_units": result.get("novel_units", []),
                    },
                )
            ),
            self._step_to_dict(
                AgentStep(
                    agent="evaluator",
                    phase="Evaluate",
                    tool="answer_evaluator",
                    thought="Verify that the knowledge action remains RBAC-bound, deduplicated, and auditable.",
                    observation={
                        "action": result.get("action"),
                        "writes_knowledge": result.get("action") in {"inserted", "inserted_merge_candidate", "merged_existing"},
                        "skipped": str(result.get("action", "")).startswith("skipped"),
                    },
                )
            ),
        ]
        return {
            **result,
            "agent": {
                "agents": self.agents(),
                "base_model": get_settings().vllm_model_name,
                "llm_reviews": llm_reviews,
                "mode": self.MODE,
                "trace": agent_trace,
            },
        }

    def _agent_review_schema(self, agent_name: str) -> dict[str, Any]:
        common = {
            "confidence": "number from 0 to 1",
            "finding": "one-sentence review result",
            "safe_to_continue": "boolean",
            "warnings": ["short warning strings"],
        }
        role_specific = {
            "evaluator": {"final_guardrail": "allow|fallback|handoff|review"},
            "intent_router": {"kind": "ops_support|controlled_operation|low_information|out_of_scope", "route_reason": "short reason"},
            "knowledge_curator": {"recommended_action": "skip|merge|candidate|insert|review"},
            "ops_employee": {"next_action_hint": "self_service|clarify|handoff"},
            "risk_guardian": {"risk_level": "low|medium|high", "required_controls": ["control names"]},
            "supervisor": {"route": "ops_support|controlled_operation|no_context_handoff|knowledge_curation"},
        }
        return {**common, **role_specific.get(agent_name, {})}

    def _llm_agent_reviews(
        self,
        *,
        task: str,
        state: dict[str, Any],
        agent_names: list[str] | None = None,
    ) -> dict[str, Any]:
        settings = get_settings()
        names = agent_names or [str(item["name"]) for item in self.AGENT_DEFINITIONS]
        if not settings.enable_agent_llm:
            return {
                "enabled": False,
                "mode": "deterministic_only",
                "reviews": {
                    name: {
                        "enabled": False,
                        "reason": "OPS_ENABLE_AGENT_LLM is false; deterministic gates remain authoritative.",
                    }
                    for name in names
                },
            }

        reviews: dict[str, Any] = {}
        max_workers = max(1, min(len(names), int(settings.agent_llm_parallelism or 1)))
        with ThreadPoolExecutor(max_workers=max_workers, thread_name_prefix="agent-review") as executor:
            futures = {executor.submit(self._run_single_llm_review, name, task, state): name for name in names}
            for future in as_completed(futures):
                name = futures[future]
                try:
                    reviews[name] = future.result()
                except Exception as exc:
                    reviews[name] = {
                        "enabled": True,
                        "ok": False,
                        "error": exc.__class__.__name__,
                        "status": "fallback_to_deterministic",
                    }
        return {
            "enabled": True,
            "mode": "llm_review_with_deterministic_authority",
            "parallelism": max_workers,
            "reviews": reviews,
        }

    def _run_single_llm_review(self, name: str, task: str, state: dict[str, Any]) -> dict[str, Any]:
        prompt = self.agent_prompts.get(name, "")
        if not prompt.strip():
            return {"enabled": True, "ok": False, "error": "prompt_missing"}
        try:
            return {
                "enabled": True,
                **llm_service.generate_agent_json(
                    agent_name=name,
                    prompt=prompt,
                    task=task,
                    state=state,
                    schema_hint=self._agent_review_schema(name),
                ),
            }
        except Exception as exc:
            return {
                "enabled": True,
                "ok": False,
                "error": exc.__class__.__name__,
                "status": "fallback_to_deterministic",
            }

    def _references(self, retrieval: Any) -> list[dict[str, Any]]:
        return [
            {
                "id": item.get("id"),
                "title": item.get("title", ""),
                "score": item.get("score", 0),
                "source_type": item.get("source_type", ""),
                "tags": item.get("tags", ""),
                "snippet": item.get("snippet", ""),
                "matched_terms": item.get("matched_terms", []),
                "match_reason": item.get("match_reason", ""),
            }
            for item in retrieval.references
        ]

    def _supervisor_route(
        self,
        question: str,
        retrieval: Any,
        draft: dict[str, Any],
        references: list[dict[str, Any]],
    ) -> dict[str, Any]:
        lowered = question.lower()
        knowledge_intent = any(keyword in question for keyword in ["沉淀", "知识库", "知识候选", "处理案例"]) or "knowledge" in lowered
        if retrieval.high_risk or draft.get("priority") == "high":
            route = "controlled_operation"
        elif not references or float(retrieval.confidence) < 0.08:
            route = "no_context_handoff"
        elif knowledge_intent:
            route = "ops_support_with_knowledge_curation"
        else:
            route = "ops_support"
        return {
            "route": route,
            "intent": draft.get("category", "general"),
            "knowledge_intent": knowledge_intent,
            "selected_agents": ["risk_guardian", "ops_employee", "knowledge_curator", "evaluator"],
            "question_preview": question[:120],
        }

    def _risk_gate(self, retrieval: Any, draft: dict[str, Any], references: list[dict[str, Any]]) -> dict[str, Any]:
        reasons: list[str] = []
        if retrieval.high_risk:
            reasons.append("controlled or destructive operation pattern matched")
        if draft.get("priority") == "high":
            reasons.append("high priority or broad impact detected")
        if not references:
            reasons.append("no reliable private knowledge reference")
        elif float(retrieval.confidence) < 0.08:
            reasons.append("retrieval confidence below safe-answer threshold")
        level = "high" if retrieval.high_risk or draft.get("priority") == "high" else "medium" if reasons else "low"
        return {
            "level": level,
            "high_risk": bool(retrieval.high_risk),
            "llm_answer_allowed": bool(references) and float(retrieval.confidence) >= 0.08 and not retrieval.high_risk,
            "reasons": reasons,
        }

    def _knowledge_curator_plan(
        self,
        question: str,
        retrieval: Any,
        draft: dict[str, Any],
        references: list[dict[str, Any]],
    ) -> dict[str, Any]:
        if retrieval.high_risk:
            action = "no_write_high_risk_request"
            reason = "High-risk requests must not be converted directly into reusable operational knowledge."
        elif not references:
            action = "wait_for_human_resolution"
            reason = "No reliable reference exists; wait for verified human resolution before curation."
        elif draft.get("missing_fields"):
            action = "candidate_after_fields_complete"
            reason = "Missing fields or resolution details must be completed before autonomous ingestion."
        else:
            action = "reuse_existing_or_autonomous_ingest_after_resolution"
            reason = "Existing references are available; future verified resolution can use autonomous dedupe and merge."
        return {
            "action": action,
            "available_tools": ["knowledge_duplicate_check", "knowledge_autonomous_ingest"],
            "reason": reason,
            "reference_count": len(references),
            "writes_now": False,
        }

    def _evaluate_answer_readiness(
        self,
        retrieval: Any,
        references: list[dict[str, Any]],
        draft: dict[str, Any],
        risk: dict[str, Any],
        curator: dict[str, Any],
    ) -> dict[str, Any]:
        llm_allowed = bool(risk.get("llm_answer_allowed"))
        return {
            "llm_allowed": llm_allowed,
            "fallback_required": not llm_allowed,
            "reference_count": len(references),
            "confidence": round(float(retrieval.confidence), 4),
            "curator_action": curator.get("action"),
            "missing_fields": draft.get("missing_fields", []),
            "checks": {
                "has_reference": bool(references),
                "not_high_risk": not bool(retrieval.high_risk),
                "confidence_ok": float(retrieval.confidence) >= 0.08,
            },
        }

    def _build_handoff_script(self, draft: dict[str, Any], high_risk: bool, has_references: bool) -> str:
        missing = draft.get("missing_fields") or []
        parts = [
            f"问题类型：{draft.get('category', 'general')}；优先级：{draft.get('priority', 'medium')}。",
            f"问题摘要：{draft.get('title', '未识别标题')}。",
        ]
        if missing:
            parts.append(f"请先补充：{'、'.join(missing)}。")
        if high_risk:
            parts.append("涉及高风险操作，只能走受控审批和人工处理，数字员工不直接执行。")
        elif not has_references:
            parts.append("当前知识库无可靠引用，建议创建在线记录由运维确认。")
        else:
            parts.append("可先按引用知识自助排查，未解决再带上日志和影响范围转人工。")
        return " ".join(parts)

    def _final_decision(
        self,
        risk: dict[str, Any],
        references: list[dict[str, Any]],
        draft: dict[str, Any],
    ) -> dict[str, Any]:
        missing = draft.get("missing_fields") or []
        if risk.get("high_risk"):
            action = "handoff_required"
            reason = "High-risk requests require authorized controlled workflow handling."
        elif not references or not risk.get("llm_answer_allowed"):
            action = "handoff_recommended"
            reason = "References are missing or confidence is too low for autonomous answering."
        elif missing:
            action = "clarify_then_self_service"
            reason = "Self-service is possible, but missing fields should be completed for traceability."
        else:
            action = "self_service_first"
            reason = "References are sufficient and no high-risk operation was detected."
        return {"action": action, "reason": reason, "missing_fields": missing}

    def _step_to_dict(self, step: AgentStep) -> dict[str, Any]:
        prompt_meta = self._agent_prompt_meta(step.agent)
        return {
            "agent": step.agent,
            "phase": step.phase,
            **prompt_meta,
            "tool": step.tool,
            "thought": step.thought,
            "observation": step.observation,
        }


agent_service = AgentService()
