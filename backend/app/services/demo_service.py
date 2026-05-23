from __future__ import annotations

import json
import uuid
from datetime import datetime
from typing import Any

from fastapi import HTTPException

from ..database import audit, connect, get_user_by_username, issue_event, rows_to_dicts, utc_now
from ..deps import ensure_row_exists
from .agent_service import agent_service
from .issues_service import build_issue_assist
from .knowledge_service import ensure_knowledge_publishable, redact_sensitive_value
from .llm_service import llm_service
from .qa_service import (
    build_employee_decision,
    build_issue_draft,
    build_issue_draft_by_rules,
    rag_service,
    serialize_rag_references,
    write_qa_message,
)

DEMO_QUESTION = "VPN 无法连接，提示证书过期，影响远程办公，电话 13800138000"
DEMO_STEPS = [
    "user_ask",
    "agent_review",
    "agent_handoff",
    "create_issue",
    "ops_accept",
    "ops_assist",
    "ops_handle",
    "user_confirm",
    "visit_and_feedback",
    "knowledge_review",
    "publish_knowledge",
    "audit_summary",
]
DEMO_SESSIONS: dict[str, dict[str, Any]] = {}


def demo_requester_user(fallback: dict[str, Any]) -> dict[str, Any]:
    return get_user_by_username("user") or fallback


def create_demo_state() -> dict[str, Any]:
    session_id = uuid.uuid4().hex[:8]
    prefix = f"[DEMO-{datetime.utcnow().strftime('%Y%m%d%H%M%S')}-{session_id}]"
    return {
        "id": session_id,
        "prefix": prefix,
        "question": DEMO_QUESTION,
        "status": "ready",
        "step_index": 0,
        "steps": DEMO_STEPS,
        "timeline": [
            {
                "role": "system",
                "title": "演示会话已创建",
                "detail": "准备按 VPN 证书过期剧本演示自助问答、转人工、处理回访、知识沉淀和审计闭环。",
                "status": "done",
                "created_at": utc_now(),
            }
        ],
        "user_window": {"messages": []},
        "agent_window": {"trace": [], "draft": {}, "answer": "", "decision": {}},
        "ops_window": {"issue": {}, "assist": {}, "solution": ""},
        "admin_window": {"knowledge": {}, "audit": [], "stats": {}},
    }


def demo_event(state: dict[str, Any], role: str, title: str, detail: str, status: str = "done") -> None:
    state["timeline"].append(
        {
            "role": role,
            "title": title,
            "detail": detail,
            "status": status,
            "created_at": utc_now(),
        }
    )


def create_demo_session() -> dict[str, Any]:
    state = create_demo_state()
    DEMO_SESSIONS[state["id"]] = state
    audit("demo_session_create", "demo", f"创建四宫格验收 Demo：{state['prefix']}")
    return state


def get_demo_session(session_id: str) -> dict[str, Any]:
    state = DEMO_SESSIONS.get(session_id)
    ensure_row_exists(state, "Demo 会话")
    return state


def run_demo_step(session_id: str, user: dict[str, Any]) -> dict[str, Any]:
    state = DEMO_SESSIONS.get(session_id)
    ensure_row_exists(state, "Demo 会话")
    if state.get("running_step"):
        return state
    if state["step_index"] >= len(DEMO_STEPS):
        state["status"] = "finished"
        return state

    step = DEMO_STEPS[state["step_index"]]
    state["running_step"] = step
    try:
        if step == "user_ask":
            run_demo_user_ask(state, user)
        elif step == "agent_review":
            run_demo_agent_review(state)
        elif step == "agent_handoff":
            run_demo_agent_handoff(state)
        elif step == "create_issue":
            run_demo_create_issue(state, user)
        elif step == "ops_accept":
            run_demo_ops_accept(state, user)
        elif step == "ops_assist":
            run_demo_ops_assist(state, user)
        elif step == "ops_handle":
            run_demo_ops_handle(state, user)
        elif step == "user_confirm":
            run_demo_user_confirm(state)
        elif step == "visit_and_feedback":
            run_demo_visit_and_feedback(state, user)
        elif step == "knowledge_review":
            run_demo_knowledge_review(state)
        elif step == "publish_knowledge":
            run_demo_publish_knowledge(state, user)
        elif step == "audit_summary":
            run_demo_audit_summary(state)
        else:
            raise HTTPException(status_code=400, detail="未知 Demo 步骤")

        state["step_index"] += 1
        state["status"] = "finished" if state["step_index"] >= len(DEMO_STEPS) else "running"
        return state
    finally:
        state["running_step"] = ""


def reset_demo_session(session_id: str) -> dict[str, Any]:
    if session_id not in DEMO_SESSIONS:
        raise HTTPException(status_code=404, detail="Demo 会话不存在")
    old_state = DEMO_SESSIONS.pop(session_id, None)
    state = create_demo_state()
    if old_state:
        demo_event(state, "system", "演示已重置", f"旧 Demo 会话 {session_id} 已清理，已创建新会话。")
    DEMO_SESSIONS[state["id"]] = state
    return state


def run_demo_user_ask(state: dict[str, Any], user: dict[str, Any]) -> None:
    if state.get("conversation_id"):
        return
    requester = demo_requester_user(user)
    question = state["question"]
    retrieval = rag_service.search(question)
    draft = build_issue_draft(question)
    agent_result = agent_service.run(question, rag_service, build_issue_draft, retrieval, draft)
    context = rag_service.build_context(retrieval.references)
    try:
        model_result = llm_service.generate(question, context, False)
    except RuntimeError:
        model_result = {
            "content": "演示兜底回答：已命中 VPN/证书相关知识。建议先检查 VPN 客户端证书有效期、重新登录客户端；如仍失败，请创建在线记录并附上错误截图或日志。",
            "status": "demo-fallback",
            "reasoning_enabled": False,
            "reasoning_available": False,
        }
    refs = serialize_rag_references(retrieval.references)
    decision = build_employee_decision(question, retrieval, refs, draft)
    answer = str(model_result["content"])
    now = utc_now()
    with connect() as conn:
        conn.execute(
            "insert into qa_logs(question,answer,need_human,model_status,references_json,created_at) values(?,?,?,?,?,?)",
            (f"{state['prefix']} {question}", answer, int(decision["need_human"]), model_result.get("status", "unknown"), json.dumps(refs, ensure_ascii=False), now),
        )
        cur = conn.execute(
            "insert into qa_conversations(user_id,title,status,created_at,updated_at) values(?,?,?,?,?)",
            (requester.get("id"), f"{state['prefix']} VPN 闭环演示", "active", now, now),
        )
        conversation_id = int(cur.lastrowid)
    write_qa_message(conversation_id, "user", question)
    write_qa_message(
        conversation_id,
        "assistant",
        answer,
        {
            "agent": agent_result,
            "automation_summary": decision["automation_summary"],
            "confidence": decision["confidence"],
            "handoff_reasons": decision["handoff_reasons"],
            "issue_draft": decision["issue_draft"],
            "missing_fields": decision["missing_fields"],
            "model_status": model_result.get("status", "unknown"),
            "need_human": decision["need_human"],
            "next_actions": decision["next_actions"],
            "references": refs,
            "risk_level": decision["risk_level"],
        },
    )
    state["conversation_id"] = conversation_id
    state["user_window"]["messages"] = [
        {"role": "user", "content": question},
        {"role": "assistant", "content": answer, "references": refs},
    ]
    state["agent_window"] = {
        "answer": answer,
        "decision": decision,
        "draft": decision["issue_draft"],
        "references": refs,
        "trace": agent_result["trace"],
        "tools_used": agent_result["tools_used"],
        "model_status": model_result.get("status", "unknown"),
    }
    demo_event(state, "agent", "数字员工完成自助问答", f"已执行 {len(agent_result['trace'])} 个 ReAct 步骤，抽取来源：{decision['issue_draft'].get('extraction_source', 'rules')}。")


def run_demo_agent_review(state: dict[str, Any]) -> None:
    if state["agent_window"].get("review_done"):
        return
    if not state.get("conversation_id"):
        raise HTTPException(status_code=400, detail="请先完成用户提问")
    references = state["agent_window"].get("references", [])
    decision = state["agent_window"].get("decision", {})
    state["agent_window"]["review_done"] = True
    state["agent_window"]["review"] = {
        "confidence": decision.get("confidence", 0),
        "handoff_reasons": decision.get("handoff_reasons", []),
        "matched_references": [item.get("title", "") for item in references[:3]],
        "risk_level": decision.get("risk_level", "medium"),
    }
    demo_event(
        state,
        "agent",
        "知识命中与风险复核",
        f"命中 {len(references)} 条 VPN/证书知识，风险级别 {decision.get('risk_level', 'medium')}，判断需要人工核实证书状态。",
    )


def run_demo_agent_handoff(state: dict[str, Any]) -> None:
    if state["agent_window"].get("handoff_done"):
        return
    draft = state["agent_window"].get("draft")
    if not draft:
        raise HTTPException(status_code=400, detail="请先完成 Agent 字段抽取")
    handoff = (
        "我已整理好转人工信息：问题类型=VPN/网络，优先级=高，影响范围=远程办公，"
        f"联系方式={draft.get('contact_phone') or '待补充'}。是否为你创建在线记录？"
    )
    state["agent_window"]["handoff_done"] = True
    state["agent_window"]["handoff_script"] = handoff
    state["user_window"].setdefault("messages", []).append({"role": "assistant", "content": handoff})
    demo_event(state, "agent", "生成转人工话术", "已把问题字段、风险原因和待补充信息整理成可审计的转人工交接单。")


def run_demo_create_issue(state: dict[str, Any], user: dict[str, Any]) -> None:
    if state.get("issue_id"):
        return
    requester = demo_requester_user(user)
    draft = state["agent_window"].get("draft") or build_issue_draft_by_rules(state["question"])
    now = utc_now()
    title = f"{state['prefix']} {draft.get('title') or 'VPN 证书过期无法连接'}"
    with connect() as conn:
        cur = conn.execute(
            """
            insert into issues(
              title,description,contact_phone,priority,status,created_at,updated_at,
              created_by,requester_name,category,impact_scope,attachment_url,log_excerpt
            ) values(?,?,?,?,?,?,?,?,?,?,?,?,?)
            """,
            (
                title,
                draft.get("description") or state["question"],
                draft.get("contact_phone", ""),
                draft.get("priority", "medium"),
                "submitted",
                now,
                now,
                requester["id"],
                requester.get("real_name") or "演示用户",
                draft.get("category", "network"),
                draft.get("impact_scope", ""),
                draft.get("attachment_url", ""),
                draft.get("log_excerpt", ""),
            ),
        )
        issue_id = int(cur.lastrowid)
        issue_event(conn, issue_id, "created", {"id": requester["id"], "real_name": requester.get("real_name", "演示用户")}, f"Demo 创建在线记录：{title}")
    audit("demo_issue_create", "issue", f"{state['prefix']} Demo 创建在线记录：{title}", issue_id)
    state["issue_id"] = issue_id
    state["ops_window"]["issue"] = {"id": issue_id, "title": title, "status": "submitted", **draft}
    demo_event(state, "user", "用户一键转人工", f"已使用云维草稿创建在线记录 #{issue_id}。")


def run_demo_ops_accept(state: dict[str, Any], user: dict[str, Any]) -> None:
    issue_id = int(state.get("issue_id") or 0)
    if not issue_id:
        raise HTTPException(status_code=400, detail="请先创建 Demo 在线记录")
    if state["ops_window"].get("accepted"):
        return
    now = utc_now()
    with connect() as conn:
        issue_event(conn, issue_id, "accepted", {"id": user["id"], "real_name": "演示运维"}, "运维人员接单，开始核验 VPN 证书与客户端状态。")
        conn.execute("update issues set status='processing',handled_by=?,accepted_at=?,updated_at=? where id=?", (user["id"], now, now, issue_id))
    audit("demo_issue_accept", "issue", f"{state['prefix']} Demo 运维接单", issue_id)
    state["ops_window"]["accepted"] = True
    state["ops_window"]["issue"] = {**state["ops_window"].get("issue", {}), "status": "processing"}
    demo_event(state, "ops", "运维人员接单", "运维人员接收在线记录，确认影响远程办公，开始核验账号、证书和客户端版本。")


def run_demo_ops_assist(state: dict[str, Any], user: dict[str, Any]) -> None:
    issue_id = int(state.get("issue_id") or 0)
    if not issue_id:
        raise HTTPException(status_code=400, detail="请先创建 Demo 在线记录")
    if state["ops_window"].get("assist"):
        return
    with connect() as conn:
        row = conn.execute(
            """
            select i.*, u.real_name as created_by_name
            from issues i
            left join users u on u.id = i.created_by
            where i.id=?
            """,
            (issue_id,),
        ).fetchone()
    ensure_row_exists(row, "在线记录")
    issue = dict(row)
    retrieval = rag_service.search(f"{issue.get('title', '')} {issue.get('description', '')} {issue.get('category', '')}", limit=5)
    assist = build_issue_assist(issue, retrieval.references)
    with connect() as conn:
        issue_event(conn, issue_id, "assist_generated", {"id": user["id"], "real_name": "演示运维"}, "已生成 AI 处理辅助、缺失字段检查和回访话术。")
    audit("demo_issue_assist", "issue", f"{state['prefix']} Demo 生成处理辅助", issue_id)
    state["ops_window"]["assist"] = assist
    demo_event(state, "ops", "生成 AI 处理辅助", "系统给出证书刷新、客户端重登、账号状态核验、回访确认等处理建议。")


def run_demo_ops_handle(state: dict[str, Any], user: dict[str, Any]) -> None:
    issue_id = int(state.get("issue_id") or 0)
    if not issue_id:
        raise HTTPException(status_code=400, detail="请先创建 Demo 在线记录")
    if state["ops_window"].get("solution"):
        return
    with connect() as conn:
        row = conn.execute(
            """
            select i.*, u.real_name as created_by_name
            from issues i
            left join users u on u.id = i.created_by
            where i.id=?
            """,
            (issue_id,),
        ).fetchone()
    ensure_row_exists(row, "在线记录")
    issue = dict(row)
    assist = state["ops_window"].get("assist")
    if not assist:
        retrieval = rag_service.search(f"{issue.get('title', '')} {issue.get('description', '')} {issue.get('category', '')}", limit=5)
        assist = build_issue_assist(issue, retrieval.references)
    solution = "演示处理结果：已核对 VPN 客户端证书缓存，指导用户重新登录并刷新证书链；用户远程办公连接恢复。"
    now = utc_now()
    with connect() as conn:
        conn.execute("update issues set solution=?,status='pending_visit',handled_by=?,handled_at=?,updated_at=? where id=?", (solution, user["id"], now, now, issue_id))
        issue_event(conn, issue_id, "handled", {"id": user["id"], "real_name": "演示运维"}, solution)
    audit("demo_issue_handle", "issue", f"{state['prefix']} Demo 运维处理：{solution}", issue_id)
    state["ops_window"]["issue"] = {**state["ops_window"].get("issue", {}), "status": "pending_visit"}
    state["ops_window"]["assist"] = assist
    state["ops_window"]["solution"] = solution
    demo_event(state, "ops", "运维处理完成", "已生成处理辅助、推荐知识和回访话术，并提交处理结果。")


def run_demo_user_confirm(state: dict[str, Any]) -> None:
    if state["user_window"].get("confirmed"):
        return
    if not state["ops_window"].get("solution"):
        raise HTTPException(status_code=400, detail="请先完成运维处理")
    state["user_window"]["confirmed"] = True
    state["user_window"].setdefault("messages", []).append(
        {
            "role": "user",
            "content": "我已按运维建议重新登录 VPN，证书刷新后可以连接了，远程办公恢复正常。",
        }
    )
    demo_event(state, "user", "用户确认恢复", "用户确认 VPN 已恢复连接，远程办公影响解除，进入回访和满意度评价。")


def run_demo_visit_and_feedback(state: dict[str, Any], user: dict[str, Any]) -> None:
    issue_id = int(state.get("issue_id") or 0)
    if not issue_id:
        raise HTTPException(status_code=400, detail="请先创建 Demo 在线记录")
    if state.get("knowledge_id"):
        return
    now = utc_now()
    visit_result = "演示回访：用户确认 VPN 已恢复，远程办公正常，满意度 5 分。"
    with connect() as conn:
        conn.execute(
            """
            update issues set resolved=1,satisfaction_score=5,visit_result=?,status='closed',
              visited_by=?,user_satisfaction_score=5,user_feedback=?,closed_at=?,updated_at=?
            where id=?
            """,
            (visit_result, user["id"], "问题已解决，处理及时。", now, now, issue_id),
        )
        issue_event(conn, issue_id, "visited", {"id": user["id"], "real_name": "演示运维"}, visit_result)
        row = conn.execute("select title,description,solution,category,log_excerpt from issues where id=?", (issue_id,)).fetchone()
        ensure_row_exists(row, "在线记录")
        content = redact_sensitive_value(f"问题现象：{row['description']}\n\n处理结果：{row['solution']}\n\n回访结论：{visit_result}")
        cur = conn.execute(
            "insert into knowledge(title,content,tags,source_type,status,created_at,updated_at) values(?,?,?,?,?,?,?)",
            (f"{state['prefix']} VPN 证书过期处理案例", content, "VPN,证书,处理案例,Demo", "case", "pending_review", now, now),
        )
        knowledge_id = int(cur.lastrowid)
        issue_event(conn, issue_id, "knowledge_candidate", {"id": user["id"], "real_name": "演示运维"}, f"已生成待审核知识候选 #{knowledge_id}")
    audit("demo_issue_visit", "issue", f"{state['prefix']} Demo 回访确认已解决", issue_id)
    audit("demo_knowledge_candidate", "knowledge", f"{state['prefix']} Demo 生成知识候选", knowledge_id)
    state["knowledge_id"] = knowledge_id
    state["ops_window"]["issue"] = {**state["ops_window"].get("issue", {}), "status": "closed", "satisfaction_score": 5}
    state["admin_window"]["knowledge"] = {"id": knowledge_id, "title": f"{state['prefix']} VPN 证书过期处理案例", "status": "pending_review"}
    demo_event(state, "ops", "回访与知识候选完成", f"用户确认已解决，满意度 5 分；生成待审核知识候选 #{knowledge_id}。")


def run_demo_knowledge_review(state: dict[str, Any]) -> None:
    knowledge_id = int(state.get("knowledge_id") or 0)
    if not knowledge_id:
        raise HTTPException(status_code=400, detail="请先生成 Demo 知识候选")
    if state["admin_window"].get("knowledge_reviewed"):
        return
    state["admin_window"]["knowledge_reviewed"] = True
    state["admin_window"]["knowledge"] = {
        **state["admin_window"].get("knowledge", {}),
        "review_notes": "已核对问题现象、处理步骤、回访结论和审计记录，符合发布条件。",
    }
    demo_event(state, "admin", "管理员审核知识候选", f"管理员复核知识候选 #{knowledge_id}，确认可沉淀为 VPN 证书过期处理案例。")


def run_demo_publish_knowledge(state: dict[str, Any], user: dict[str, Any]) -> None:
    knowledge_id = int(state.get("knowledge_id") or 0)
    if not knowledge_id:
        raise HTTPException(status_code=400, detail="请先生成 Demo 知识候选")
    if state["admin_window"].get("knowledge", {}).get("status") == "published":
        return
    now = utc_now()
    with connect() as conn:
        row = conn.execute("select title,content,tags,status from knowledge where id=?", (knowledge_id,)).fetchone()
        ensure_row_exists(row, "知识条目")
        ensure_knowledge_publishable(row["title"], row["content"], row["tags"])
        conn.execute(
            "update knowledge set status='published',reviewed_by=?,reviewed_at=?,review_note=?,updated_at=? where id=?",
            (user["id"], now, "Demo 审核通过并发布", now, knowledge_id),
        )
    audit("demo_knowledge_publish", "knowledge", f"{state['prefix']} Demo 管理员发布知识：{row['title']}", knowledge_id)
    state["admin_window"]["knowledge"] = {"id": knowledge_id, "title": row["title"], "status": "published", "updated_at": now}
    demo_event(state, "admin", "管理员审核发布知识", f"知识候选 #{knowledge_id} 已发布，后续同类问题可进入 RAG 检索。")


def run_demo_audit_summary(state: dict[str, Any]) -> None:
    if state["admin_window"].get("audit"):
        return
    with connect() as conn:
        audit_rows = conn.execute(
            "select * from audit_logs where content like ? order by id desc limit 12",
            (f"%{state['prefix']}%",),
        ).fetchall()
        qa_rows = conn.execute(
            "select * from qa_logs where question like ? order by id desc limit 5",
            (f"%{state['prefix']}%",),
        ).fetchall()
        stats_snapshot = {
            "qa_logs": conn.execute("select count(*) from qa_logs").fetchone()[0],
            "issues": conn.execute("select count(*) from issues").fetchone()[0],
            "closed_issues": conn.execute("select count(*) from issues where status='closed'").fetchone()[0],
            "pending_knowledge": conn.execute("select count(*) from knowledge where status='pending_review'").fetchone()[0],
            "published_knowledge": conn.execute("select count(*) from knowledge where status='published'").fetchone()[0],
            "audit_logs": conn.execute("select count(*) from audit_logs").fetchone()[0],
        }
    state["admin_window"]["audit"] = rows_to_dicts(audit_rows)
    state["admin_window"]["qa_logs"] = rows_to_dicts(qa_rows)
    state["admin_window"]["stats"] = stats_snapshot
    demo_event(state, "auditor", "审计统计完成", f"已汇总 {len(audit_rows)} 条 Demo 审计日志和当前系统指标。")
