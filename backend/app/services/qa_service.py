from __future__ import annotations

import json
import re
from typing import Any

from fastapi import HTTPException

from ..database import audit, connect, rows_to_dicts, utc_now
from ..deps import ensure_row_exists
from ..schemas import QuestionRequest
from .agent_service import agent_service
from .answer_synthesis_service import build_evidence_brief, build_response_plan
from .intent_router_service import IntentRoute, intent_router_service
from .llm_service import llm_service
from .rag_service import rag_service

ISSUE_CATEGORY_KEYWORDS = {
    "account": ["账号", "密码", "登录", "权限", "冻结", "解冻", "用户"],
    "network": ["vpn", "网络", "连接", "证书", "远程", "wifi", "专线"],
    "business": ["系统", "应用", "页面", "业务", "接口", "访问慢", "报错"],
    "database": ["数据库", "mysql", "oracle", "redis", "连接池", "sql", "中间件"],
}
HIGH_PRIORITY_KEYWORDS = [
    "生产", "全公司", "全部", "大面积", "中断", "无法访问", "宕机", "紧急", "批量", "高优先级",
    "多人", "多名", "多个用户", "同部门", "部门多人", "业务截止",
]
LOW_PRIORITY_KEYWORDS = ["咨询", "了解", "低优先级", "不紧急"]
RAG_EVAL_CASES = [
    {"query": "VPN 提示证书过期，远程办公无法连接", "expected": ["VPN", "证书"]},
    {"query": "账号被冻结提示锁定，怎么恢复登录", "expected": ["账号", "冻结", "锁定"]},
    {"query": "申请业务系统权限需要准备哪些审批信息", "expected": ["权限", "审批"]},
    {"query": "数据库连接失败需要先排查什么", "expected": ["数据库", "连接"]},
    {"query": "处理完成后怎么沉淀到知识库", "expected": ["知识库", "沉淀"]},
    {"query": "MFA 验证码收不到，手机验证失败怎么办", "expected": ["MFA", "验证码", "多因素认证"]},
    {"query": "Outlook 一直离线，收不到邮件怎么处理", "expected": ["Outlook", "邮箱", "邮件"]},
    {"query": "打印机任务卡在队列里，无法打印怎么办", "expected": ["打印机", "打印"]},
    {"query": "公司 Wi-Fi 连不上，DNS 解析失败怎么排查", "expected": ["Wi-Fi", "网络", "DNS"]},
    {"query": "业务系统白屏并提示 500 或 502 超时", "expected": ["业务系统", "500", "502"]},
]


def build_issue_draft_by_rules(description: str) -> dict[str, Any]:
    text = description.strip()
    lowered = text.lower()
    category = "general"
    for item, keywords in ISSUE_CATEGORY_KEYWORDS.items():
        if any(keyword in lowered or keyword in text for keyword in keywords):
            category = item
            break
    priority = "medium"
    if any(keyword in text for keyword in HIGH_PRIORITY_KEYWORDS):
        priority = "high"
    elif any(keyword in text for keyword in LOW_PRIORITY_KEYWORDS):
        priority = "low"

    phone_match = re.search(r"(?:1[3-9]\d{9}|0\d{2,3}-?\d{7,8})", text)
    url_match = re.search(r"(?:https?://|ftp://|file://|/)[^\s，。；;]+", text)
    log_lines = [
        line.strip()
        for line in text.splitlines()
        if any(mark in line.lower() for mark in ["error", "exception", "failed", "timeout", "traceback", "报错", "失败", "超时"])
    ]
    impact_scope = ""
    for marker in ["影响范围", "影响", "范围"]:
        match = re.search(rf"{marker}[:：]?\s*([^。；;\n]+)", text)
        if match:
            impact_scope = match.group(1).strip()
            break
    if not impact_scope:
        if any(keyword in text for keyword in ["全公司", "全部用户", "大面积"]):
            impact_scope = "全公司/大面积影响"
        elif any(keyword in text for keyword in ["部门", "多人", "批量"]):
            impact_scope = "部门或多人受影响"
        elif any(keyword in text for keyword in ["我", "单人", "个人"]):
            impact_scope = "单人受影响"

    title = re.split(r"[。；;\n]", text)[0][:40] or "在线记录"
    missing_fields = []
    if not phone_match:
        missing_fields.append("联系方式")
    if not impact_scope:
        missing_fields.append("影响范围")
    if not url_match:
        missing_fields.append("截图/附件链接")
    if not log_lines:
        missing_fields.append("错误日志或报错原文")
    return {
        "title": title,
        "description": text,
        "category": category,
        "priority": priority,
        "impact_scope": impact_scope,
        "contact_phone": phone_match.group(0) if phone_match else "",
        "attachment_url": url_match.group(0) if url_match else "",
        "log_excerpt": "\n".join(log_lines)[:1000],
        "missing_fields": missing_fields,
        "confidence": 0.78 if category != "general" else 0.52,
        "extraction_source": "rules",
    }


def build_issue_draft(description: str, use_llm: bool = True) -> dict[str, Any]:
    rule_draft = build_issue_draft_by_rules(description)
    if not use_llm:
        return rule_draft
    try:
        draft = llm_service.extract_issue_draft(description, rule_draft)
        if rule_draft.get("priority") == "high" and draft.get("priority") != "high":
            draft = {**draft, "priority": "high"}
        return draft
    except RuntimeError as exc:
        return {
            **rule_draft,
            "extraction_source": "rules_fallback",
            "extraction_error": str(exc).split(":", 1)[0],
        }


def build_employee_decision(
    question: str,
    retrieval: Any,
    references: list[dict[str, Any]],
    draft: dict[str, Any] | None = None,
) -> dict[str, Any]:
    draft = draft or build_issue_draft(question)
    if retrieval.high_risk:
        risk_level = "high"
    elif draft["priority"] == "high":
        risk_level = "high"
    elif retrieval.confidence >= 0.25 and references:
        risk_level = "low"
    else:
        risk_level = "medium"

    need_human = retrieval.high_risk or draft["priority"] == "high" or retrieval.confidence < 0.08 or not references
    handoff_reasons = []
    if retrieval.high_risk:
        handoff_reasons.append("涉及高风险账号、权限、生产或批量操作，需要人工受控处理")
    if not references:
        handoff_reasons.append("私有知识库没有命中可引用知识")
    elif retrieval.confidence < 0.08:
        handoff_reasons.append("知识命中置信度较低，需要人工确认")
    if draft["priority"] == "high" and not retrieval.high_risk:
        handoff_reasons.append("影响范围或紧急程度较高，需要运维人员跟进确认")
    if draft["missing_fields"]:
        handoff_reasons.append(f"缺少关键信息：{'、'.join(draft['missing_fields'])}")

    next_actions = []
    if references and not retrieval.high_risk:
        next_actions.append({"key": "self_check", "label": "按知识库步骤自助处理", "enabled": True})
    if references:
        next_actions.append({"key": "view_references", "label": "查看引用知识", "enabled": True})
    if draft["missing_fields"]:
        next_actions.append({"key": "complete_fields", "label": "补充缺失字段", "enabled": True})
    next_actions.append({"key": "create_issue", "label": "创建在线记录", "enabled": True})
    if draft["category"] == "account" or retrieval.high_risk:
        next_actions.append({"key": "controlled_workflow", "label": "走受控账号/人工审批流程", "enabled": True})

    clarification_templates = {
        "联系方式": "请补充联系电话或企业 IM 联系方式，便于运维人员回访。",
        "影响范围": "请补充影响范围：仅你本人、部分同事、整个部门，还是大面积用户？",
        "截图/附件链接": "请补充截图、附件或共享路径，用于定位具体报错界面。",
        "错误日志或报错原文": "请复制错误提示、日志关键行或失败时间点。",
    }
    clarification_questions = [
        clarification_templates.get(field, f"请补充 {field}。")
        for field in draft["missing_fields"]
    ]
    automation_summary = [
        f"已识别问题类型：{draft['category']}",
        f"已评估风险等级：{risk_level}",
        f"已检索私有知识库：命中 {len(references)} 条，最高置信度 {round(float(retrieval.confidence), 4)}",
    ]
    if draft["missing_fields"]:
        automation_summary.append(f"已发现待补充字段：{'、'.join(draft['missing_fields'])}")
    if need_human:
        automation_summary.append("已准备在线记录草稿，可一键转人工处理")

    return {
        "intent": draft["category"],
        "intent_label": {
            "account": "账号与权限",
            "business": "业务系统",
            "database": "数据库/中间件",
            "general": "通用咨询",
            "network": "网络/VPN",
        }.get(draft["category"], "通用咨询"),
        "risk_level": risk_level,
        "confidence": round(float(retrieval.confidence), 4),
        "need_human": need_human,
        "handoff_reasons": handoff_reasons,
        "missing_fields": draft["missing_fields"],
        "clarification_questions": clarification_questions,
        "automation_summary": automation_summary,
        "next_actions": next_actions,
        "issue_draft": {
            "title": draft["title"],
            "description": draft["description"],
            "priority": "high" if need_human or risk_level == "high" else draft["priority"],
            "category": draft["category"],
            "impact_scope": draft["impact_scope"],
            "contact_phone": draft["contact_phone"],
            "attachment_url": draft["attachment_url"],
            "log_excerpt": draft["log_excerpt"],
            "missing_fields": draft["missing_fields"],
            "confidence": draft.get("confidence", 0),
            "extraction_source": draft.get("extraction_source", "rules"),
            "extraction_error": draft.get("extraction_error", ""),
            "llm_status": draft.get("llm_status", ""),
        },
    }


def serialize_rag_references(references: list[dict[str, Any]]) -> list[dict[str, Any]]:
    payload = []
    for item in references:
        payload.append(
            {
                "id": item.get("id"),
                "title": item.get("title", ""),
                "tags": item.get("tags", ""),
                "source_type": item.get("source_type", ""),
                "version": item.get("version", 1),
                "score": item.get("score", 0),
                "snippet": item.get("snippet", ""),
                "matched_terms": item.get("matched_terms", []),
                "match_reason": item.get("match_reason", ""),
                "retrieval_stage": item.get("retrieval_stage", ""),
                "score_detail": item.get("score_detail", {}),
                "updated_at": item.get("updated_at", ""),
            }
        )
    return payload


def build_no_context_answer(decision: dict[str, Any]) -> str:
    missing = decision.get("missing_fields") or []
    missing_text = "、".join(missing) if missing else "问题现象、影响范围、联系方式和错误截图/日志"
    return (
        "我暂时没有找到足够可靠的企业知识来直接给结论。\n\n"
        f"为了让运维人员更快定位，请补充：{missing_text}。\n"
        "如果问题比较急，可以直接提交在线记录，我会把已填写的信息带过去。"
    )


def build_controlled_operation_answer(decision: dict[str, Any], references: list[dict[str, Any]]) -> str:
    reasons = decision.get("handoff_reasons") or ["涉及高风险账号、权限、生产或批量操作，需要人工受控处理"]
    missing = decision.get("missing_fields") or []
    missing_text = "、".join(missing) if missing else "申请人身份、审批依据、目标账号/权限范围、影响范围和回退方案"
    return (
        "1) 结论：该请求涉及受控账号、权限或生产变更，数字员工不能直接执行，也不能提供绕过审批的操作步骤。\n"
        "2) 处理步骤：请创建在线记录或账号审批单，由授权运维/管理员核验身份、审批依据、影响范围和回退方案后处理。\n"
        f"3) 需要补充的信息：{missing_text}。\n"
        f"4) 是否建议转人工：必须转人工。原因：{'；'.join(str(item) for item in reasons)}。"
    )


def strip_inline_reference_section(answer: str) -> str:
    lines = str(answer or "").rstrip().splitlines()
    if not lines:
        return ""

    def normalized_header(line: str) -> str:
        return re.sub(r"[\s*#：:]+", "", line.strip())

    for index in range(len(lines) - 1, -1, -1):
        header = normalized_header(lines[index])
        if header in {"引用", "引用来源", "参考", "参考来源", "来源", "Sources", "References"}:
            return "\n".join(lines[:index]).rstrip()

    citation_tail = 0
    for line in reversed(lines):
        cleaned = line.strip()
        if not cleaned:
            citation_tail += 1
            continue
        if re.search(r"(引用来源|参考来源|knowledge_id|知识[_-]?id|source\s*id|references?|sources?)", cleaned, flags=re.I):
            citation_tail += 1
            continue
        break
    if citation_tail:
        return "\n".join(lines[: len(lines) - citation_tail]).rstrip()
    return "\n".join(lines).rstrip()


def parse_message_metadata(value: str) -> dict[str, Any]:
    try:
        parsed = json.loads(value or "{}")
    except json.JSONDecodeError:
        return {}
    return parsed if isinstance(parsed, dict) else {}


def load_qa_conversation(conn: Any, conversation_id: int, user: dict[str, Any]) -> Any:
    row = conn.execute("select * from qa_conversations where id=?", (conversation_id,)).fetchone()
    ensure_row_exists(row, "数字员工会话")
    if row["deleted_at"]:
        raise HTTPException(status_code=404, detail="数字员工会话已删除")
    if user.get("role") == "user" and row["user_id"] != user.get("id"):
        raise HTTPException(status_code=403, detail="只能查看自己的数字员工会话")
    return row


def load_qa_conversation_for_write(conn: Any, conversation_id: int, user: dict[str, Any]) -> Any:
    row = conn.execute("select * from qa_conversations where id=?", (conversation_id,)).fetchone()
    ensure_row_exists(row, "数字员工会话")
    if row["deleted_at"]:
        raise HTTPException(status_code=404, detail="数字员工会话已删除")
    if row["user_id"] != user.get("id"):
        raise HTTPException(status_code=403, detail="只能继续自己的数字员工会话；管理、运维和审计查看他人会话为只读")
    return row


def prepare_qa_conversation(data: QuestionRequest, user: dict[str, Any]) -> tuple[int, str]:
    now = utc_now()
    with connect() as conn:
        if data.conversation_id:
            row = load_qa_conversation_for_write(conn, data.conversation_id, user)
            conversation_id = int(row["id"])
            conn.execute("update qa_conversations set updated_at=? where id=?", (now, conversation_id))
        else:
            cur = conn.execute(
                "insert into qa_conversations(user_id,title,status,created_at,updated_at) values(?,?,?,?,?)",
                (user.get("id"), data.question[:40], "active", now, now),
            )
            conversation_id = int(cur.lastrowid)
        rows = conn.execute(
            """
            select role,content from qa_messages
            where conversation_id=?
            order by id desc
            limit 8
            """,
            (conversation_id,),
        ).fetchall()
    history = list(reversed(rows))
    if not history:
        return conversation_id, data.question
    history_text = "\n".join(f"{row['role']}: {row['content'][:500]}" for row in history)
    return conversation_id, f"历史对话：\n{history_text}\n\n当前用户补充/问题：\n{data.question}"


def write_qa_message(conversation_id: int, role: str, content: str, metadata: dict[str, Any] | None = None) -> None:
    with connect() as conn:
        conn.execute(
            "insert into qa_messages(conversation_id,role,content,metadata_json,created_at) values(?,?,?,?,?)",
            (conversation_id, role, content, json.dumps(metadata or {}, ensure_ascii=False), utc_now()),
        )
        conn.execute("update qa_conversations set updated_at=? where id=?", (utc_now(), conversation_id))


def build_intent_route_response(data: QuestionRequest, conversation_id: int, route: IntentRoute) -> dict[str, Any]:
    answer = route.answer
    issue_draft = intent_router_service.empty_issue_draft(data.question)
    agent_result = intent_router_service.agent_result(route)
    rag_meta = {
        "confidence": 0.0,
        "query_terms": [],
        "strategy": "intent_router_no_rag",
    }
    response = {
        "conversation_id": conversation_id,
        "answer": answer,
        "references": [],
        "rag": rag_meta,
        "need_human": route.should_handoff,
        "intent": route.intent,
        "intent_label": route.intent_label,
        "risk_level": route.risk_level,
        "confidence": route.confidence,
        "missing_fields": [],
        "clarification_questions": [],
        "automation_summary": [],
        "handoff_reasons": [],
        "model_status": "intent-router",
        "llm_used": route.source == "llm",
        "reasoning_enabled": False,
        "reasoning_available": False,
        "agent": agent_result,
        "employee": {
            "name": "云维",
            "role": "企业运维数字员工",
            "mode": "intent_router",
        },
        "next_actions": route.next_actions,
        "issue_draft": issue_draft,
        "intent_route": route.to_metadata(),
    }
    metadata = {
        "automation_summary": [],
        "clarification_questions": [],
        "confidence": route.confidence,
        "handoff_reasons": [],
        "intent": route.intent,
        "intent_label": route.intent_label,
        "intent_route": route.to_metadata(),
        "issue_draft": issue_draft,
        "missing_fields": [],
        "model_status": "intent-router",
        "llm_used": route.source == "llm",
        "risk_level": route.risk_level,
        "need_human": route.should_handoff,
        "next_actions": route.next_actions,
        "references": [],
        "rag": rag_meta,
        "reasoning_available": False,
        "reasoning_enabled": False,
        "agent": agent_result,
        "interaction_type": route.kind,
    }
    with connect() as conn:
        conn.execute(
            "insert into qa_logs(question,answer,need_human,model_status,references_json,created_at) values(?,?,?,?,?,?)",
            (data.question, answer, int(route.should_handoff), "intent-router", "[]", utc_now()),
        )
    write_qa_message(conversation_id, "user", data.question)
    write_qa_message(conversation_id, "assistant", answer, metadata)
    return response


def ask_question(data: QuestionRequest, user: dict[str, Any]) -> dict[str, Any]:
    conversation_id, effective_question = prepare_qa_conversation(data, user)
    intent_route = intent_router_service.route(data.question)
    if not intent_route.should_rag:
        return build_intent_route_response(data, conversation_id, intent_route)

    result = rag_service.search(effective_question)
    refs = serialize_rag_references(result.references)
    should_extract_with_llm = bool(refs) and result.confidence >= 0.08 and not result.high_risk
    draft = build_issue_draft(effective_question, use_llm=should_extract_with_llm)
    agent_result = agent_service.run(
        effective_question,
        rag_service,
        build_issue_draft,
        result,
        draft,
        intent_route.to_metadata(),
    )
    should_use_llm = bool(agent_result.get("evaluator", {}).get("llm_allowed"))
    decision = build_employee_decision(effective_question, result, refs, draft)
    evidence = build_evidence_brief(result.references)
    answer_plan = build_response_plan(
        question=data.question,
        retrieval=result,
        references=refs,
        draft=draft,
        decision=decision,
        intent_route=intent_route.to_metadata(),
        evidence=evidence,
    )
    ranked_context = rag_service.build_context(result.references)
    context = "\n\n".join(
        item
        for item in [
            f"Evidence brief:\n{evidence['brief']}" if evidence.get("brief") else "",
            f"Ranked reference context:\n{ranked_context}" if ranked_context else "",
        ]
        if item
    )
    try:
        model_result = (
            llm_service.generate(effective_question, context, data.enable_thinking, answer_plan=answer_plan)
            if should_use_llm
            else {
                "content": build_controlled_operation_answer(decision, refs)
                if result.high_risk
                else build_no_context_answer(decision),
                "reasoning_available": False,
                "reasoning_enabled": False,
                "status": "controlled-fallback" if result.high_risk else "rag-fallback",
            }
        )
    except RuntimeError as exc:
        raise HTTPException(status_code=503, detail=f"LLM 数字员工不可用：{exc}") from exc
    answer = model_result["content"]
    if refs:
        answer = strip_inline_reference_section(answer)
    need_human = decision["need_human"]
    if need_human:
        if result.high_risk:
            answer += "\n\n我可以继续帮你整理在线记录，由授权运维人员按流程处理。"
        elif (
            model_result.get("status") != "rag-fallback"
            and answer_plan.get("append_handoff_hint")
            and "在线记录" not in answer
        ):
            answer += "\n\n你可以继续补充信息；如果比较急，也可以直接提交在线记录。"
    with connect() as conn:
        conn.execute(
            "insert into qa_logs(question,answer,need_human,model_status,references_json,created_at) values(?,?,?,?,?,?)",
            (data.question, answer, int(need_human), model_result.get("status", "unknown"), json.dumps(refs, ensure_ascii=False), utc_now()),
        )
    write_qa_message(conversation_id, "user", data.question)
    write_qa_message(
        conversation_id,
        "assistant",
        answer,
        {
            "automation_summary": decision["automation_summary"],
            "clarification_questions": decision["clarification_questions"],
            "confidence": decision["confidence"],
            "handoff_reasons": decision["handoff_reasons"],
            "intent": decision["intent"],
            "intent_label": decision["intent_label"],
            "issue_draft": decision["issue_draft"],
            "missing_fields": decision["missing_fields"],
            "model_status": model_result.get("status", "unknown"),
            "llm_used": should_use_llm,
            "risk_level": decision["risk_level"],
            "need_human": need_human,
            "next_actions": decision["next_actions"],
            "references": refs,
            "rag": {
                "confidence": round(float(result.confidence), 4),
                "query_terms": result.query_terms,
                "strategy": result.strategy,
            },
            "reasoning_available": model_result.get("reasoning_available", False),
            "reasoning_enabled": model_result.get("reasoning_enabled", False),
            "agent": agent_result,
            "evidence": evidence,
            "intent_route": intent_route.to_metadata(),
            "response_plan": answer_plan,
        },
    )
    return {
        "conversation_id": conversation_id,
        "answer": answer,
        "references": refs,
        "rag": {
            "confidence": round(float(result.confidence), 4),
            "query_terms": result.query_terms,
            "strategy": result.strategy,
        },
        "need_human": need_human,
        "intent": decision["intent"],
        "intent_label": decision["intent_label"],
        "risk_level": decision["risk_level"],
        "confidence": decision["confidence"],
        "missing_fields": decision["missing_fields"],
        "clarification_questions": decision["clarification_questions"],
        "automation_summary": decision["automation_summary"],
        "handoff_reasons": decision["handoff_reasons"],
        "model_status": model_result.get("status", "unknown"),
        "llm_used": should_use_llm,
        "reasoning_enabled": model_result.get("reasoning_enabled", False),
        "reasoning_available": model_result.get("reasoning_available", False),
        "agent": agent_result,
        "employee": {
            "name": "云维",
            "role": "企业运维数字员工",
            "mode": "llm",
        },
        "evidence": evidence,
        "next_actions": decision["next_actions"],
        "issue_draft": decision["issue_draft"],
        "intent_route": intent_route.to_metadata(),
        "response_plan": answer_plan,
    }


def suggest_knowledge(q: str, limit: int) -> list[dict[str, Any]]:
    return rag_service.suggest(q, limit)


def evaluate_rag() -> dict[str, Any]:
    cases = []
    passed = 0
    strategy = "qwen3_embedding_hybrid_rerank"
    for case in RAG_EVAL_CASES:
        retrieval = rag_service.search(case["query"], limit=3)
        strategy = retrieval.strategy
        refs = serialize_rag_references(retrieval.references)
        evidence_text = " ".join(
            [
                *(item.get("title", "") for item in refs),
                *(item.get("snippet", "") for item in refs),
                *(" ".join(item.get("matched_terms", [])) for item in refs),
            ]
        )
        ok = bool(refs) and any(term.lower() in evidence_text.lower() for term in case["expected"])
        passed += int(ok)
        cases.append(
            {
                "query": case["query"],
                "expected": case["expected"],
                "passed": ok,
                "confidence": retrieval.confidence,
                "query_terms": retrieval.query_terms,
                "references": refs,
            }
        )
    return {
        "strategy": strategy,
        "total": len(cases),
        "passed": passed,
        "pass_rate": passed / len(cases) if cases else 0,
        "cases": cases,
    }


def list_qa_conversations(limit: int, user: dict[str, Any]) -> list[dict[str, Any]]:
    params: list[Any] = []
    where_parts = ["c.deleted_at=''"]
    if user.get("role") == "user":
        where_parts.append("c.user_id=?")
        params.append(user["id"])
    where = "where " + " and ".join(where_parts)
    with connect() as conn:
        rows = conn.execute(
            f"""
            select
              c.*,
              u.real_name as user_name,
              count(m.id) as message_count,
              max(m.created_at) as last_message_at,
              (
                select m2.content
                from qa_messages m2
                where m2.conversation_id = c.id
                order by m2.id desc
                limit 1
              ) as last_message
            from qa_conversations c
            left join users u on u.id = c.user_id
            left join qa_messages m on m.conversation_id = c.id
            {where}
            group by c.id
            order by c.updated_at desc, c.id desc
            limit ?
            """,
            [*params, limit],
        ).fetchall()
    conversations = rows_to_dicts(rows)
    for item in conversations:
        preview = (item.get("last_message") or item.get("title") or "").replace("\n", " ").strip()
        item["last_message"] = preview[:120]
    return conversations


def get_qa_conversation(conversation_id: int, user: dict[str, Any]) -> dict[str, Any]:
    with connect() as conn:
        row = load_qa_conversation(conn, conversation_id, user)
        conversation = dict(row)
        owner = conn.execute("select real_name from users where id=?", (conversation.get("user_id"),)).fetchone()
        if owner:
            conversation["user_name"] = owner["real_name"]
        message_rows = conn.execute(
            """
            select id,role,content,metadata_json,created_at
            from qa_messages
            where conversation_id=?
            order by id asc
            """,
            (conversation_id,),
        ).fetchall()
    messages = []
    for row in message_rows:
        item = dict(row)
        item["metadata"] = parse_message_metadata(item.pop("metadata_json", "{}"))
        messages.append(item)
    return {"conversation": conversation, "messages": messages}


def delete_qa_conversation(conversation_id: int, user: dict[str, Any]) -> dict[str, Any]:
    now = utc_now()
    with connect() as conn:
        row = conn.execute("select * from qa_conversations where id=?", (conversation_id,)).fetchone()
        ensure_row_exists(row, "数字员工会话")
        if row["deleted_at"]:
            return {"deleted": True, "deleted_at": row["deleted_at"], "id": conversation_id}
        if row["user_id"] != user.get("id"):
            raise HTTPException(status_code=403, detail="只能删除自己的数字员工会话")
        conn.execute(
            "update qa_conversations set status='deleted',deleted_at=?,updated_at=? where id=?",
            (now, now, conversation_id),
        )
    audit("qa_conversation_delete", "qa_conversation", f"用户删除咨询会话：#{conversation_id}", conversation_id)
    return {"deleted": True, "deleted_at": now, "id": conversation_id}
