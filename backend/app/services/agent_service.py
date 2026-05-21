from __future__ import annotations

import importlib
from dataclasses import dataclass, field
from typing import Any, Callable, Protocol


class SearchService(Protocol):
    def search(self, question: str, limit: int = 4) -> Any:
        ...


DraftBuilder = Callable[[str], dict[str, Any]]


@dataclass
class AgentStep:
    phase: str
    tool: str
    thought: str
    observation: dict[str, Any] = field(default_factory=dict)


class AgentService:
    """Safe low-risk ReAct prototype for Yunwei.

    The service exposes only read-only or draft-generation actions. It does not
    execute account changes, production commands, database restarts, or any
    other high-risk operation.
    """

    SAFE_TOOLS = {"knowledge_search", "issue_draft", "handoff_script"}

    def status(self) -> dict[str, Any]:
        error = ""
        try:
            importlib.import_module("qwen_agent")
            available = True
        except Exception as exc:
            available = False
            error = exc.__class__.__name__
        return {
            "mode": "controlled_react_prototype",
            "qwen_agent_available": available,
            "qwen_agent_error": error,
            "tools": sorted(self.SAFE_TOOLS),
            "safety_boundary": "only read knowledge, draft issues, and generate handoff scripts",
        }

    def run(
        self,
        question: str,
        rag_service: SearchService,
        draft_builder: DraftBuilder,
        retrieval: Any | None = None,
        draft: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        trace: list[AgentStep] = []
        trace.append(
            AgentStep(
                phase="Reason",
                tool="planner",
                thought="识别用户意图、风险和需要调用的低风险工具。",
                observation={"question_preview": question[:120]},
            )
        )

        retrieval = retrieval or rag_service.search(question, limit=4)
        references = [
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
        trace.append(
            AgentStep(
                phase="Act",
                tool="knowledge_search",
                thought="从已发布私有知识库检索可引用的处理依据。",
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

        draft = draft or draft_builder(question)
        trace.append(
            AgentStep(
                phase="Act",
                tool="issue_draft",
                thought="结构化抽取在线记录草稿字段，仅生成草稿，不创建真实工单。",
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
                phase="Act",
                tool="handoff_script",
                thought="生成转人工/回访沟通话术，便于用户和运维继续处理。",
                observation={"script": handoff_script},
            )
        )

        next_decision = self._final_decision(retrieval.high_risk, retrieval.confidence, references, draft)
        trace.append(
            AgentStep(
                phase="Final",
                tool="planner",
                thought="综合观察结果，决定自助、补字段或转人工。",
                observation=next_decision,
            )
        )

        return {
            "mode": "controlled_react_prototype",
            "tools_used": [step.tool for step in trace if step.tool in self.SAFE_TOOLS],
            "trace": [self._step_to_dict(step) for step in trace],
            "issue_draft": draft,
            "handoff_script": handoff_script,
            "decision": next_decision,
            "qwen_agent_status": self.status(),
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
        high_risk: bool,
        confidence: float,
        references: list[dict[str, Any]],
        draft: dict[str, Any],
    ) -> dict[str, Any]:
        missing = draft.get("missing_fields") or []
        if high_risk:
            action = "handoff_required"
            reason = "高风险请求必须由授权人员通过受控流程处理"
        elif not references or confidence < 0.08:
            action = "handoff_recommended"
            reason = "知识库引用不足或置信度过低"
        elif missing:
            action = "clarify_then_self_service"
            reason = "可先自助处理，但需要补充关键信息以便追踪"
        else:
            action = "self_service_first"
            reason = "知识命中较明确且未识别高风险操作"
        return {"action": action, "reason": reason, "missing_fields": missing}

    def _step_to_dict(self, step: AgentStep) -> dict[str, Any]:
        return {
            "phase": step.phase,
            "tool": step.tool,
            "thought": step.thought,
            "observation": step.observation,
        }


agent_service = AgentService()
