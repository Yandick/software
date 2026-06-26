from __future__ import annotations

import re
from typing import Any

SECTION_NAMES = [
    "现象",
    "原因",
    "处理步骤",
    "自助步骤",
    "排查步骤",
    "恢复方式",
    "转人工条件",
    "注意事项",
    "处理结果",
    "结论",
]
QUESTION_MODE_KEYWORDS = {
    "diagnostic_judgement": ["判断", "更像", "是不是", "是否", "还是", "属于", "需不需要", "要不要"],
    "knowledge_explanation": ["为什么", "原因", "原理", "区别", "什么是", "解释"],
    "self_service_steps": ["怎么办", "怎么处理", "如何处理", "怎么排查", "先检查", "步骤", "恢复"],
}
OPERATOR_ACTION_KEYWORDS = [
    "回滚",
    "切回",
    "发布",
    "网关",
    "数据库",
    "重启",
    "配置",
    "权限",
    "清理缓存",
    "接口日志",
    "应用日志",
]
INTERNAL_GOVERNANCE_KEYWORDS = [
    "处理知识中",
    "知识中不能",
    "知识入库",
    "发布前",
    "敏感信息检查",
    "重复检查",
    "知识审核",
    "知识候选",
]


def _compact_text(value: Any, limit: int = 360) -> str:
    text = re.sub(r"\s+", " ", str(value or "")).strip()
    if len(text) <= limit:
        return text
    return text[: limit - 1].rstrip() + "..."


def _contains_any(text: str, keywords: list[str]) -> bool:
    lowered = text.lower()
    return any(keyword.lower() in lowered for keyword in keywords)


def _split_sentences(text: str) -> list[str]:
    return [
        item.strip()
        for item in re.split(r"(?<=[。！？；;.!?])\s*|\n+", str(text or ""))
        if item.strip()
    ]


def _clean_evidence_value(value: str) -> str:
    sentences = _split_sentences(value)
    visible = [
        sentence
        for sentence in sentences
        if not any(keyword in sentence for keyword in INTERNAL_GOVERNANCE_KEYWORDS)
    ]
    return _compact_text(" ".join(visible) if visible else value, 520)


def extract_evidence_sections(content: str) -> dict[str, str]:
    """Extract common operations-knowledge sections from semi-structured Chinese text."""
    pattern = re.compile(rf"({'|'.join(re.escape(name) for name in SECTION_NAMES)})\s*[:：]")
    matches = list(pattern.finditer(content or ""))
    sections: dict[str, str] = {}
    if matches:
        for index, match in enumerate(matches):
            name = match.group(1)
            start = match.end()
            end = matches[index + 1].start() if index + 1 < len(matches) else len(content)
            value = _clean_evidence_value(content[start:end])
            if value:
                sections[name] = value
        return sections

    sentences = _split_sentences(content)
    if sentences:
        sections["摘要"] = _clean_evidence_value(" ".join(sentences[:3]))
    return sections


def _section_lines(sections: dict[str, str]) -> list[str]:
    preferred_order = ["现象", "原因", "结论", "处理步骤", "自助步骤", "排查步骤", "恢复方式", "转人工条件", "注意事项", "处理结果", "摘要"]
    lines = []
    for name in preferred_order:
        value = sections.get(name)
        if value:
            lines.append(f"- {name}: {value}")
    return lines


def build_evidence_brief(references: list[dict[str, Any]], limit: int = 4) -> dict[str, Any]:
    items: list[dict[str, Any]] = []
    blocks: list[str] = []
    for rank, item in enumerate(references[:limit], start=1):
        sections = extract_evidence_sections(str(item.get("content", "")))
        role = "primary" if rank == 1 else "secondary"
        evidence = {
            "id": item.get("id"),
            "rank": rank,
            "role": role,
            "score": item.get("score", 0),
            "source_type": item.get("source_type", ""),
            "title": item.get("title", ""),
            "sections": sections,
            "snippet": item.get("snippet", ""),
        }
        items.append(evidence)
        label = "Primary evidence" if rank == 1 else f"Secondary evidence {rank}"
        section_lines = _section_lines(sections)
        if not section_lines and item.get("snippet"):
            section_lines = [f"- 命中片段: {_compact_text(item.get('snippet'), 260)}"]
        blocks.append(
            "\n".join(
                [
                    f"{label}:",
                    f"- 标题: {item.get('title', '')}",
                    *section_lines[:8],
                ]
            )
        )
    return {"brief": "\n\n".join(blocks), "items": items}


def build_response_plan(
    *,
    question: str,
    retrieval: Any,
    references: list[dict[str, Any]],
    draft: dict[str, Any],
    decision: dict[str, Any],
    intent_route: dict[str, Any],
    evidence: dict[str, Any],
) -> dict[str, Any]:
    mode = "concise_resolution"
    for candidate, keywords in QUESTION_MODE_KEYWORDS.items():
        if _contains_any(question, keywords):
            mode = candidate
            break
    primary_evidence = (evidence.get("items") or [{}])[0] if evidence.get("items") else {}
    primary_sections = primary_evidence.get("sections") if isinstance(primary_evidence, dict) else {}
    primary_source_type = str(primary_evidence.get("source_type", "") if isinstance(primary_evidence, dict) else "")
    primary_score = float(primary_evidence.get("score", 0) or 0) if isinstance(primary_evidence, dict) else 0.0
    if not references or float(getattr(retrieval, "confidence", 0.0)) < 0.08:
        mode = "clarification_first"
    elif bool(getattr(retrieval, "high_risk", False)):
        mode = "controlled_handoff"
    elif (
        mode in {"diagnostic_judgement", "incident_handoff", "concise_resolution"}
        and primary_source_type in {"case", "runbook"}
        and primary_score >= 0.45
        and any(primary_sections.get(name) for name in ["现象", "原因", "处理步骤", "恢复方式", "注意事项"])
    ):
        mode = "case_matched_incident_judgement"
    elif mode == "concise_resolution" and decision.get("need_human"):
        mode = "incident_handoff"

    plan_by_mode = {
        "clarification_first": {
            "purpose": "Ask only for the missing fields needed to continue; do not pretend to know the answer.",
            "sections": ["需要补充什么", "为什么需要", "下一步"],
            "style": "short, direct, no generic troubleshooting list",
        },
        "controlled_handoff": {
            "purpose": "Explain the control boundary and route the user to authorized handling.",
            "sections": ["受控结论", "必须走的流程", "需要材料"],
            "style": "firm and concise; no executable privileged steps",
        },
        "diagnostic_judgement": {
            "purpose": "Judge the most likely issue type and explain the evidence.",
            "sections": ["判断", "依据", "现在先做", "是否提交在线记录"],
            "style": "reasoned judgement first; avoid long checklists",
        },
        "case_matched_incident_judgement": {
            "purpose": "Use the primary matched case to judge the incident and make the knowledge-base change visible.",
            "sections": ["判断", "命中案例", "用户现在做", "运维处理方向", "是否提交在线记录"],
            "style": "case-grounded triage; focus end-user actions on evidence collection and online-record submission; explicitly separate operations-staff actions",
        },
        "self_service_steps": {
            "purpose": "Give practical steps separated by user actions and operations-staff actions.",
            "sections": ["先做什么", "用户可自助", "运维执行", "失败后升级"],
            "style": "action-oriented, avoid repeating obvious metadata",
        },
        "knowledge_explanation": {
            "purpose": "Explain the mechanism, applicable conditions, and cautions from the knowledge base.",
            "sections": ["解释", "适用条件", "注意事项"],
            "style": "explanatory, not a ticket template",
        },
        "incident_handoff": {
            "purpose": "Summarize the incident and guide the user to submit an online record with useful fields.",
            "sections": ["初步结论", "建议记录的信息", "运维处理方向"],
            "style": "compact incident triage",
        },
        "concise_resolution": {
            "purpose": "Answer directly using the strongest evidence, only adding handoff guidance if needed.",
            "sections": ["答案", "操作要点"],
            "style": "natural, concise, evidence-grounded",
        },
    }
    plan = {
        "mode": mode,
        **plan_by_mode[mode],
        "avoid": [
            "Do not use one universal five-part template.",
            "Do not include a missing-information section when no important field is missing.",
            "Do not mechanically repeat all retrieved references.",
            "Do not add a stock closing sentence unless this plan explicitly asks for handoff guidance.",
            "Do not place rollback, gateway cache cleanup, release changes, service restarts, or server-side log inspection under end-user actions.",
        ],
        "append_handoff_hint": mode in {"diagnostic_judgement", "case_matched_incident_judgement", "incident_handoff"} and bool(decision.get("need_human")),
        "missing_fields": decision.get("missing_fields", []),
        "intent": decision.get("intent") or intent_route.get("intent") or draft.get("category", "general"),
    }
    if primary_evidence:
        plan["primary_evidence"] = {
            "title": primary_evidence.get("title", ""),
            "source_type": primary_source_type,
            "score": round(primary_score, 4),
            "available_sections": [name for name in ["现象", "原因", "处理步骤", "恢复方式", "注意事项", "摘要"] if primary_sections.get(name)],
        }
    if mode == "case_matched_incident_judgement" and decision.get("risk_level") == "high":
        plan["case_mode_rule"] = (
            "For a high-impact matched incident, user actions should emphasize submitting an online record and collecting "
            "time, URL, screenshot, error code, impact scope, and business deadline. Do not suggest repeated local browser "
            "troubleshooting unless the primary case explicitly requires it."
        )
    if _contains_any(" ".join(_section_lines(primary_sections or {})), OPERATOR_ACTION_KEYWORDS):
        plan["operator_action_rule"] = "Mark rollback, cache cleanup, log inspection, restart, permission, and production changes as operations-staff actions."
    return plan
