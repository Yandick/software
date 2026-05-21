from __future__ import annotations

import csv
import io
import json
import re
import uuid
from datetime import datetime
from pathlib import Path
from typing import Any

from fastapi import Depends, FastAPI, File, HTTPException, Query, UploadFile
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse

from .config import get_settings
from .database import audit, connect, init_db, issue_event, rows_to_dicts, utc_now, write_audit
from .schemas import (
    AccountCreate,
    AccountApprovalCreate,
    AccountApprovalDecision,
    AccountUpdate,
    IssueCreate,
    IssueDraftRequest,
    IssueFeedback,
    IssueHandle,
    IssueStatusUpdate,
    IssueVisit,
    KnowledgeCreate,
    KnowledgeStatusUpdate,
    LoginRequest,
    QuestionRequest,
)
from .security import create_access_token, current_user, verify_password
from .services.agent_service import agent_service
from .services.llm_service import llm_service
from .services.rag_service import RagService

app = FastAPI(title="运维数字员工系统")
rag_service = RagService()
UPLOAD_DIR = Path("backend/data/uploads")
ALLOWED_UPLOAD_SUFFIXES = {".jpg", ".jpeg", ".png", ".gif", ".webp", ".txt", ".log", ".pdf", ".zip"}
MAX_UPLOAD_BYTES = 10 * 1024 * 1024
KNOWLEDGE_STATUSES = {"pending_review", "published", "offline"}
KNOWLEDGE_SOURCE_TYPES = {"faq", "runbook", "case", "policy", "other"}
ACCOUNT_RISK_LEVELS = {"low", "medium", "high"}
ACCOUNT_UPDATE_FIELDS = {
    "owner_name",
    "department",
    "contact_phone",
    "permission_scope",
    "risk_level",
    "expires_at",
    "remark",
    "status",
}
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
ISSUE_CATEGORY_KEYWORDS = {
    "account": ["账号", "密码", "登录", "权限", "冻结", "解冻", "用户"],
    "network": ["vpn", "网络", "连接", "证书", "远程", "wifi", "专线"],
    "business": ["系统", "应用", "页面", "业务", "接口", "访问慢", "报错"],
    "database": ["数据库", "mysql", "oracle", "redis", "连接池", "sql", "中间件"],
}
HIGH_PRIORITY_KEYWORDS = ["生产", "全公司", "全部", "大面积", "中断", "无法访问", "宕机", "紧急", "批量", "高优先级"]
LOW_PRIORITY_KEYWORDS = ["咨询", "了解", "低优先级", "不紧急"]
ISSUE_STATUSES = {
    "submitted",
    "accepted",
    "processing",
    "need_user_info",
    "pending_visit",
    "closed",
    # Legacy values kept for existing demo/dev data.
    "pending",
    "handled",
}
ISSUE_OPERATOR_STATUSES = {"accepted", "processing", "need_user_info"}
ISSUE_ACTIVE_STATUSES = {"submitted", "accepted", "processing", "need_user_info", "pending", "handled"}
ISSUE_PENDING_VISIT_STATUSES = {"pending_visit", "handled"}
ISSUE_STATUS_LABELS = {
    "accepted": "已受理",
    "closed": "已关闭",
    "handled": "待回访",
    "need_user_info": "待用户补充",
    "pending": "待处理",
    "pending_visit": "待回访",
    "processing": "处理中",
    "submitted": "已提交",
}

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)
UPLOAD_DIR.mkdir(parents=True, exist_ok=True)


@app.on_event("startup")
def startup() -> None:
    init_db()


def require_roles(user: dict[str, Any], roles: set[str]) -> None:
    if user.get("role") not in roles:
        raise HTTPException(status_code=403, detail="Permission denied")


def public_user(user: dict[str, Any]) -> dict[str, Any]:
    return {key: user[key] for key in ["id", "username", "real_name", "role", "department", "status"] if key in user}


def ensure_row_exists(row: Any, target: str = "记录") -> None:
    if not row:
        raise HTTPException(status_code=404, detail=f"{target}不存在")


def parse_utc_datetime(value: str) -> datetime | None:
    if not value:
        return None
    try:
        return datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None


def elapsed_minutes(start: str, end: str) -> int | None:
    start_dt = parse_utc_datetime(start)
    end_dt = parse_utc_datetime(end)
    if not start_dt or not end_dt:
        return None
    return max(0, int((end_dt - start_dt).total_seconds() // 60))


def validate_issue_status(status: str, allowed: set[str] | None = None) -> None:
    valid = allowed or ISSUE_STATUSES
    if status not in valid:
        labels = "、".join(ISSUE_STATUS_LABELS[item] for item in sorted(valid) if item in ISSUE_STATUS_LABELS)
        raise HTTPException(status_code=400, detail=f"在线记录状态只能是：{labels}")


def enrich_issue_item(item: dict[str, Any]) -> dict[str, Any]:
    now = utc_now()
    item["status_label"] = ISSUE_STATUS_LABELS.get(item.get("status", ""), item.get("status", ""))
    item["response_minutes"] = elapsed_minutes(item.get("created_at", ""), item.get("accepted_at", ""))
    item["handling_minutes"] = elapsed_minutes(item.get("accepted_at", ""), item.get("handled_at", ""))
    item["total_minutes"] = elapsed_minutes(item.get("created_at", ""), item.get("closed_at", "") or now)
    return item


def validate_knowledge_payload(data: KnowledgeCreate | KnowledgeStatusUpdate) -> None:
    if getattr(data, "status", "") not in KNOWLEDGE_STATUSES:
        raise HTTPException(status_code=400, detail="知识状态只能是 pending_review、published 或 offline")
    source_type = getattr(data, "source_type", None)
    if source_type is not None and source_type not in KNOWLEDGE_SOURCE_TYPES:
        raise HTTPException(status_code=400, detail="知识来源类型不合法")


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
        return llm_service.extract_issue_draft(description, rule_draft)
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

    need_human = retrieval.high_risk or retrieval.confidence < 0.08 or not references
    handoff_reasons = []
    if retrieval.high_risk:
        handoff_reasons.append("涉及高风险账号、权限、生产或批量操作，需要人工受控处理")
    if not references:
        handoff_reasons.append("私有知识库没有命中可引用知识")
    elif retrieval.confidence < 0.08:
        handoff_reasons.append("知识命中置信度较低，需要人工确认")
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
                "score_detail": item.get("score_detail", {}),
                "updated_at": item.get("updated_at", ""),
            }
        )
    return payload


def build_issue_assist(issue: dict[str, Any], references: list[dict[str, Any]]) -> dict[str, Any]:
    text = f"{issue.get('title', '')}\n{issue.get('description', '')}\n{issue.get('log_excerpt', '')}"
    missing = []
    if not issue.get("contact_phone"):
        missing.append("联系方式")
    if not issue.get("impact_scope"):
        missing.append("影响范围")
    if not issue.get("log_excerpt"):
        missing.append("错误日志")
    if not issue.get("attachment_url"):
        missing.append("截图/附件")
    suggestions = [
        "先确认影响范围、出现时间、用户账号和是否可复现。",
        "按推荐知识逐条核对自助步骤和标准处理流程。",
        "涉及账号冻结、权限变更、生产数据、批量操作时，只能走受控后台流程并记录审计。",
    ]
    if issue.get("category") == "network":
        suggestions.extend(["检查 VPN/网络客户端版本、证书有效期、本地网络连通性和最近变更。"])
    elif issue.get("category") == "account":
        suggestions.extend(["核对账号状态、密码策略、权限申请单和最近登录失败记录。"])
    elif issue.get("category") == "database":
        suggestions.extend(["确认连接串、账号权限、数据库监听状态、连接池和错误日志时间点。"])
    elif issue.get("category") == "business":
        suggestions.extend(["确认业务系统模块、接口报错、浏览器/客户端版本和是否有发布变更。"])

    return {
        "summary": f"{issue.get('requester_name') or issue.get('created_by_name') or '用户'} 提交了 {issue.get('category') or 'general'} 类问题：{issue.get('title')}。优先级为 {issue.get('priority')}，当前状态为 {issue.get('status')}。",
        "risk_notes": [
            "AI 辅助内容仅供运维人员参考，不能替代人工判断。",
            "高风险账号和生产操作必须由有权限人员在受控后台确认执行。",
        ],
        "missing_fields": missing,
        "suggested_steps": suggestions,
        "recommended_knowledge": [
            {
                "id": item["id"],
                "title": item["title"],
                "tags": item.get("tags", ""),
                "score": item.get("score", 0),
                "source_type": item.get("source_type", ""),
                "matched_terms": item.get("matched_terms", []),
                "match_reason": item.get("match_reason", ""),
                "content_preview": item.get("snippet") or str(item.get("content", ""))[:180],
            }
            for item in references
        ],
        "visit_script": (
            f"您好，关于您反馈的“{issue.get('title')}”，我们已完成处理。"
            "请您确认当前问题是否已经解决，业务是否恢复正常；如仍有异常，请补充最新报错、影响范围和截图/日志。"
        ),
        "knowledge_candidate": {
            "title": f"处理案例：{issue.get('title')}",
            "tags": f"{issue.get('category') or 'general'},处理案例",
            "content": f"问题现象：{issue.get('description')}\n\n处理建议：\n" + "\n".join(f"- {step}" for step in suggestions),
        },
    }


def safe_upload_name(filename: str) -> tuple[str, str]:
    original = Path(filename or "upload.bin").name
    suffix = Path(original).suffix.lower()
    if suffix not in ALLOWED_UPLOAD_SUFFIXES:
        raise HTTPException(status_code=400, detail="附件类型不支持，请上传图片、txt/log、PDF 或 zip")
    stem = re.sub(r"[^A-Za-z0-9_.-]+", "_", Path(original).stem).strip("._") or "attachment"
    return original, f"{uuid.uuid4().hex}_{stem}{suffix}"


def normalize_account_payload(action: str, payload: dict[str, Any]) -> dict[str, Any]:
    if action != "update":
        return {}
    fields = {key: value for key, value in payload.items() if key in ACCOUNT_UPDATE_FIELDS and value is not None}
    if "status" in fields and fields["status"] not in {"active", "frozen"}:
        raise HTTPException(status_code=400, detail="账号状态只能是 active 或 frozen")
    if "risk_level" in fields and fields["risk_level"] not in ACCOUNT_RISK_LEVELS:
        raise HTTPException(status_code=400, detail="账号风险等级只能是 low、medium 或 high")
    return fields


def validate_account_create(data: AccountCreate) -> None:
    if data.risk_level not in ACCOUNT_RISK_LEVELS:
        raise HTTPException(status_code=400, detail="账号风险等级只能是 low、medium 或 high")


def account_expiry_meta(expires_at: str) -> dict[str, Any]:
    if not expires_at:
        return {"days_to_expire": None, "expiry_status": "none"}
    try:
        expire_date = datetime.strptime(expires_at[:10], "%Y-%m-%d").date()
    except ValueError:
        return {"days_to_expire": None, "expiry_status": "invalid"}
    days = (expire_date - datetime.utcnow().date()).days
    if days < 0:
        status = "expired"
    elif days <= 30:
        status = "expiring"
    else:
        status = "valid"
    return {"days_to_expire": days, "expiry_status": status}


def account_row_to_dict(row: Any) -> dict[str, Any]:
    item = dict(row)
    item.update(account_expiry_meta(item.get("expires_at", "")))
    return item


def fetch_account_rows(conn: Any, q: str = "") -> list[Any]:
    if q:
        return conn.execute(
            """
            select * from ops_accounts
            where account_name like ?
               or owner_name like ?
               or department like ?
               or contact_phone like ?
               or permission_scope like ?
               or risk_level like ?
               or remark like ?
            order by id desc
            """,
            (f"%{q}%", f"%{q}%", f"%{q}%", f"%{q}%", f"%{q}%", f"%{q}%", f"%{q}%"),
        ).fetchall()
    return conn.execute("select * from ops_accounts order by id desc").fetchall()


def build_accounts_csv(accounts: list[dict[str, Any]]) -> str:
    output = io.StringIO()
    output.write("\ufeff")
    fields = [
        ("id", "ID"),
        ("account_name", "账号名"),
        ("owner_name", "负责人"),
        ("department", "部门"),
        ("contact_phone", "联系方式"),
        ("permission_scope", "权限范围"),
        ("status", "状态"),
        ("risk_level", "风险等级"),
        ("expires_at", "有效期"),
        ("expiry_status", "到期状态"),
        ("days_to_expire", "剩余天数"),
        ("remark", "备注"),
        ("created_at", "创建时间"),
        ("updated_at", "更新时间"),
    ]
    writer = csv.DictWriter(output, fieldnames=[key for key, _ in fields], extrasaction="ignore")
    writer.writerow({key: label for key, label in fields})
    for account in accounts:
        writer.writerow(account)
    return output.getvalue()


def parse_message_metadata(value: str) -> dict[str, Any]:
    try:
        parsed = json.loads(value or "{}")
    except json.JSONDecodeError:
        return {}
    return parsed if isinstance(parsed, dict) else {}


def load_qa_conversation(conn: Any, conversation_id: int, user: dict[str, Any]) -> Any:
    row = conn.execute("select * from qa_conversations where id=?", (conversation_id,)).fetchone()
    ensure_row_exists(row, "数字员工会话")
    if user.get("role") == "user" and row["user_id"] != user.get("id"):
        raise HTTPException(status_code=403, detail="只能查看自己的数字员工会话")
    return row


def load_qa_conversation_for_write(conn: Any, conversation_id: int, user: dict[str, Any]) -> Any:
    row = conn.execute("select * from qa_conversations where id=?", (conversation_id,)).fetchone()
    ensure_row_exists(row, "数字员工会话")
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


def extract_attachment_ids(attachment_url: str) -> list[int]:
    ids = re.findall(r"/api/issues/attachments/(\d+)/download", attachment_url or "")
    return sorted({int(item) for item in ids})


def validate_issue_attachment_refs(conn: Any, attachment_url: str, user: dict[str, Any]) -> None:
    if not attachment_url:
        return
    for attachment_id in extract_attachment_ids(attachment_url):
        row = conn.execute("select uploaded_by,issue_id from issue_attachments where id=?", (attachment_id,)).fetchone()
        ensure_row_exists(row, "附件")
        if row["uploaded_by"] != user.get("id") or row["issue_id"] is not None:
            raise HTTPException(status_code=403, detail="只能关联自己上传且尚未绑定工单的附件")


def bind_issue_attachments(conn: Any, issue_id: int, attachment_url: str, user: dict[str, Any]) -> None:
    for attachment_id in extract_attachment_ids(attachment_url):
        conn.execute(
            "update issue_attachments set issue_id=? where id=? and uploaded_by=? and issue_id is null",
            (issue_id, attachment_id, user.get("id")),
        )


def write_qa_message(conversation_id: int, role: str, content: str, metadata: dict[str, Any] | None = None) -> None:
    with connect() as conn:
        conn.execute(
            "insert into qa_messages(conversation_id,role,content,metadata_json,created_at) values(?,?,?,?,?)",
            (conversation_id, role, content, json.dumps(metadata or {}, ensure_ascii=False), utc_now()),
        )
        conn.execute("update qa_conversations set updated_at=? where id=?", (utc_now(), conversation_id))


def create_account_approval_record(
    conn: Any,
    account_id: int,
    action: str,
    payload: dict[str, Any],
    reason: str,
    user: dict[str, Any],
) -> int:
    if action not in {"freeze", "unfreeze", "update"}:
        raise HTTPException(status_code=400, detail="审批动作只能是 freeze、unfreeze 或 update")
    row = conn.execute("select id from ops_accounts where id=?", (account_id,)).fetchone()
    ensure_row_exists(row, "运维账号")
    safe_payload = normalize_account_payload(action, payload)
    now = utc_now()
    cur = conn.execute(
        """
        insert into account_approvals(account_id,action,payload_json,reason,status,requested_by,created_at,updated_at)
        values(?,?,?,?,?,?,?,?)
        """,
        (account_id, action, json.dumps(safe_payload, ensure_ascii=False), reason, "pending", user.get("id"), now, now),
    )
    approval_id = int(cur.lastrowid)
    write_audit(conn, "account_approval_create", "account_approval", f"创建账号审批：{action} account={account_id}", approval_id)
    return approval_id


def apply_account_action(conn: Any, approval: dict[str, Any], operator: dict[str, Any]) -> None:
    now = utc_now()
    account_id = int(approval["account_id"])
    action = approval["action"]
    payload = json.loads(approval.get("payload_json") or "{}")
    row = conn.execute("select account_name,status from ops_accounts where id=?", (account_id,)).fetchone()
    ensure_row_exists(row, "运维账号")
    if action == "freeze":
        if row["status"] == "frozen":
            raise HTTPException(status_code=400, detail="账号已冻结")
        conn.execute("update ops_accounts set status='frozen',updated_at=? where id=?", (now, account_id))
        write_audit(conn, "account_freeze", "ops_account", f"{operator.get('real_name', '')} 审批后冻结运维账号：{row['account_name']}", account_id)
    elif action == "unfreeze":
        if row["status"] == "active":
            raise HTTPException(status_code=400, detail="账号已是启用状态")
        conn.execute("update ops_accounts set status='active',updated_at=? where id=?", (now, account_id))
        write_audit(conn, "account_unfreeze", "ops_account", f"{operator.get('real_name', '')} 审批后解冻运维账号：{row['account_name']}", account_id)
    elif action == "update":
        fields = normalize_account_payload(action, payload)
        if fields:
            assignments = ",".join(f"{key}=?" for key in fields)
            conn.execute(f"update ops_accounts set {assignments},updated_at=? where id=?", [*fields.values(), now, account_id])
            write_audit(conn, "account_update", "ops_account", f"{operator.get('real_name', '')} 审批后修改运维账号：{fields}", account_id)
    else:
        raise HTTPException(status_code=400, detail="不支持的账号审批动作")


@app.get("/api/health")
def health() -> dict[str, Any]:
    return {"status": "ok", "app": get_settings().app_name}


@app.get("/api/llm/status")
def llm_status(_: dict[str, Any] = Depends(current_user)) -> dict[str, Any]:
    return llm_service.status()


@app.get("/api/agent/status")
def agent_status(_: dict[str, Any] = Depends(current_user)) -> dict[str, Any]:
    return agent_service.status()


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


@app.post("/api/demo/session")
def create_demo_session(user: dict[str, Any] = Depends(current_user)) -> dict[str, Any]:
    require_roles(user, {"admin"})
    state = create_demo_state()
    DEMO_SESSIONS[state["id"]] = state
    audit("demo_session_create", "demo", f"创建四宫格验收 Demo：{state['prefix']}")
    return state


@app.get("/api/demo/session/{session_id}")
def get_demo_session(session_id: str, user: dict[str, Any] = Depends(current_user)) -> dict[str, Any]:
    require_roles(user, {"admin", "ops"})
    state = DEMO_SESSIONS.get(session_id)
    ensure_row_exists(state, "Demo 会话")
    return state


@app.post("/api/demo/session/{session_id}/step")
def run_demo_step(session_id: str, user: dict[str, Any] = Depends(current_user)) -> dict[str, Any]:
    require_roles(user, {"admin"})
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


@app.post("/api/demo/session/{session_id}/reset")
def reset_demo_session(session_id: str, user: dict[str, Any] = Depends(current_user)) -> dict[str, Any]:
    require_roles(user, {"admin"})
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
            (user.get("id"), f"{state['prefix']} VPN 闭环演示", "active", now, now),
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
                user["id"],
                "演示用户",
                draft.get("category", "network"),
                draft.get("impact_scope", ""),
                draft.get("attachment_url", ""),
                draft.get("log_excerpt", ""),
            ),
        )
        issue_id = int(cur.lastrowid)
        issue_event(conn, issue_id, "created", {"id": user["id"], "real_name": "演示用户"}, f"Demo 创建在线记录：{title}")
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
        content = f"问题现象：{row['description']}\n\n处理结果：{row['solution']}\n\n回访结论：{visit_result}"
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
        row = conn.execute("select title,status from knowledge where id=?", (knowledge_id,)).fetchone()
        ensure_row_exists(row, "知识条目")
        conn.execute("update knowledge set status='published',updated_at=? where id=?", (now, knowledge_id))
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


@app.post("/api/auth/login")
def login(data: LoginRequest) -> dict[str, Any]:
    from .database import get_user_by_username

    user = get_user_by_username(data.username)
    if not user or not verify_password(data.password, user["password_hash"]):
        audit("login_failed", "user", f"登录失败：{data.username}")
        raise HTTPException(status_code=401, detail="用户名或密码错误")
    token = create_access_token(user["username"], {"role": user["role"]})
    audit("login", "user", f"用户登录：{user['username']}", user["id"])
    return {"access_token": token, "token_type": "bearer", "user": public_user(user)}


@app.get("/api/auth/me")
def me(user: dict[str, Any] = Depends(current_user)) -> dict[str, Any]:
    return public_user(user)


@app.post("/api/auth/refresh")
def refresh(user: dict[str, Any] = Depends(current_user)) -> dict[str, Any]:
    return {"data": create_access_token(user["username"], {"role": user["role"]}), "status": 0}


@app.get("/api/menu/all")
def menus(_: dict[str, Any] = Depends(current_user)) -> list[dict[str, Any]]:
    return []


@app.post("/api/qa/ask")
def ask(data: QuestionRequest, user: dict[str, Any] = Depends(current_user)) -> dict[str, Any]:
    conversation_id, effective_question = prepare_qa_conversation(data, user)
    result = rag_service.search(effective_question)
    draft = build_issue_draft(effective_question)
    agent_result = agent_service.run(effective_question, rag_service, build_issue_draft, result, draft)
    context = rag_service.build_context(result.references)
    try:
        model_result = llm_service.generate(effective_question, context, data.enable_thinking)
    except RuntimeError as exc:
        raise HTTPException(status_code=503, detail=f"LLM 数字员工不可用：{exc}") from exc
    answer = model_result["content"]
    refs = serialize_rag_references(result.references)
    decision = build_employee_decision(effective_question, result, refs, draft)
    need_human = decision["need_human"]
    if need_human:
        answer += "\n\n系统判断该问题建议转人工：请创建在线记录，补充影响范围、联系方式和错误截图/日志。"
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
        "llm_used": True,
        "reasoning_enabled": model_result.get("reasoning_enabled", False),
        "reasoning_available": model_result.get("reasoning_available", False),
        "agent": agent_result,
        "employee": {
            "name": "云维",
            "role": "企业运维数字员工",
            "mode": "llm",
        },
        "next_actions": decision["next_actions"],
        "issue_draft": decision["issue_draft"],
    }


@app.get("/api/qa/suggest")
def suggest(q: str = "", limit: int = Query(8, ge=1, le=20), user: dict[str, Any] = Depends(current_user)) -> list[dict[str, Any]]:
    return rag_service.suggest(q, limit)


RAG_EVAL_CASES = [
    {"query": "VPN 提示证书过期，远程办公无法连接", "expected": ["VPN", "证书"]},
    {"query": "账号被冻结提示锁定，怎么恢复登录", "expected": ["账号", "冻结", "锁定"]},
    {"query": "申请业务系统权限需要准备哪些审批信息", "expected": ["权限", "审批"]},
    {"query": "数据库连接失败需要先排查什么", "expected": ["数据库", "连接"]},
    {"query": "处理完成后怎么沉淀到知识库", "expected": ["知识库", "沉淀"]},
]


@app.get("/api/rag/evaluate")
def evaluate_rag(user: dict[str, Any] = Depends(current_user)) -> dict[str, Any]:
    require_roles(user, {"admin", "ops", "auditor"})
    cases = []
    passed = 0
    for case in RAG_EVAL_CASES:
        retrieval = rag_service.search(case["query"], limit=3)
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
        "strategy": "hybrid_keyword_chunk",
        "total": len(cases),
        "passed": passed,
        "pass_rate": passed / len(cases) if cases else 0,
        "cases": cases,
    }


@app.get("/api/qa/conversations")
def list_qa_conversations(
    limit: int = Query(20, ge=1, le=100),
    user: dict[str, Any] = Depends(current_user),
) -> list[dict[str, Any]]:
    params: list[Any] = []
    where = ""
    if user.get("role") == "user":
        where = "where c.user_id=?"
        params.append(user["id"])
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


@app.get("/api/qa/conversations/{conversation_id}")
def get_qa_conversation(conversation_id: int, user: dict[str, Any] = Depends(current_user)) -> dict[str, Any]:
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


@app.post("/api/issues/draft")
def draft_issue(data: IssueDraftRequest, user: dict[str, Any] = Depends(current_user)) -> dict[str, Any]:
    draft = build_issue_draft(data.description)
    audit("issue_draft", "issue", f"生成在线记录草稿：{draft['title']}", None)
    return draft


@app.post("/api/issues/attachments")
def upload_issue_attachment(file: UploadFile = File(...), user: dict[str, Any] = Depends(current_user)) -> dict[str, Any]:
    original_name, stored_name = safe_upload_name(file.filename or "")
    target = UPLOAD_DIR / stored_name
    size = 0
    try:
        with target.open("wb") as output:
            while True:
                chunk = file.file.read(1024 * 1024)
                if not chunk:
                    break
                size += len(chunk)
                if size > MAX_UPLOAD_BYTES:
                    output.close()
                    target.unlink(missing_ok=True)
                    raise HTTPException(status_code=400, detail="附件不能超过 10MB")
                output.write(chunk)
    finally:
        file.file.close()
    now = utc_now()
    with connect() as conn:
        cur = conn.execute(
            """
            insert into issue_attachments(original_name,stored_name,content_type,size,uploaded_by,created_at)
            values(?,?,?,?,?,?)
            """,
            (original_name, stored_name, file.content_type or "application/octet-stream", size, user.get("id"), now),
        )
        attachment_id = int(cur.lastrowid)
    url = f"/api/issues/attachments/{attachment_id}/download"
    audit("issue_attachment_upload", "issue_attachment", f"上传在线记录附件：{original_name} ({size} bytes)", attachment_id)
    return {
        "id": attachment_id,
        "filename": original_name,
        "size": size,
        "url": url,
        "content_type": file.content_type or "application/octet-stream",
    }


@app.get("/api/issues/attachments/{attachment_id}/download")
def download_issue_attachment(attachment_id: int, user: dict[str, Any] = Depends(current_user)) -> FileResponse:
    with connect() as conn:
        row = conn.execute("select * from issue_attachments where id=?", (attachment_id,)).fetchone()
        ensure_row_exists(row, "附件")
        can_access = row["uploaded_by"] == user.get("id") or user.get("role") in {"admin", "ops", "auditor"}
        if row["issue_id"] and not can_access:
            issue_row = conn.execute("select created_by,handled_by,visited_by from issues where id=?", (row["issue_id"],)).fetchone()
            can_access = bool(
                issue_row
                and (
                    issue_row["created_by"] == user.get("id")
                    or issue_row["handled_by"] == user.get("id")
                    or issue_row["visited_by"] == user.get("id")
                )
            )
    if not can_access:
        raise HTTPException(status_code=403, detail="无权下载该附件")
    path = UPLOAD_DIR / row["stored_name"]
    if not path.exists():
        raise HTTPException(status_code=404, detail="附件文件不存在")
    audit("issue_attachment_download", "issue_attachment", f"下载在线记录附件：{row['original_name']}", attachment_id)
    return FileResponse(path, media_type=row["content_type"], filename=row["original_name"])


@app.get("/api/knowledge")
def list_knowledge(
    q: str = "",
    status: str = "",
    source_type: str = "",
    user: dict[str, Any] = Depends(current_user),
) -> list[dict[str, Any]]:
    with connect() as conn:
        params: list[Any] = []
        where: list[str] = []
        if user.get("role") == "user":
            where.append("status='published'")
        elif status:
            where.append("status=?")
            params.append(status)
        if q:
            where.append("(title like ? or content like ? or tags like ?)")
            params.extend([f"%{q}%", f"%{q}%", f"%{q}%"])
        if source_type:
            where.append("source_type=?")
            params.append(source_type)
        where_sql = f"where {' and '.join(where)}" if where else ""
        rows = conn.execute(f"select * from knowledge {where_sql} order by id desc", params).fetchall()
    return rows_to_dicts(rows)


@app.post("/api/knowledge")
def create_knowledge(data: KnowledgeCreate, user: dict[str, Any] = Depends(current_user)) -> dict[str, Any]:
    require_roles(user, {"admin", "ops"})
    validate_knowledge_payload(data)
    payload = data.model_dump()
    if user.get("role") == "ops":
        payload["status"] = "pending_review"
    now = utc_now()
    with connect() as conn:
        cur = conn.execute(
            """
            insert into knowledge(
              title,content,tags,source_type,status,version,reviewed_by,reviewed_at,review_note,created_at,updated_at
            ) values(?,?,?,?,?,?,?,?,?,?,?)
            """,
            (
                payload["title"],
                payload["content"],
                payload["tags"],
                payload["source_type"],
                payload["status"],
                1,
                user["id"] if payload["status"] == "published" else None,
                now if payload["status"] == "published" else None,
                "创建时直接发布" if payload["status"] == "published" else "",
                now,
                now,
            ),
        )
        item_id = int(cur.lastrowid)
    audit("knowledge_create", "knowledge", f"{user.get('real_name','')}新增知识：{payload['title']}，状态：{payload['status']}", item_id)
    return {
        "id": item_id,
        **payload,
        "created_at": now,
        "review_note": "创建时直接发布" if payload["status"] == "published" else "",
        "reviewed_at": now if payload["status"] == "published" else None,
        "reviewed_by": user["id"] if payload["status"] == "published" else None,
        "updated_at": now,
        "version": 1,
    }


@app.put("/api/knowledge/{item_id}")
def update_knowledge(item_id: int, data: KnowledgeCreate, user: dict[str, Any] = Depends(current_user)) -> dict[str, Any]:
    require_roles(user, {"admin", "ops"})
    validate_knowledge_payload(data)
    payload = data.model_dump()
    now = utc_now()
    with connect() as conn:
        row = conn.execute("select id,status,version from knowledge where id=?", (item_id,)).fetchone()
        ensure_row_exists(row, "知识条目")
        if user.get("role") == "ops":
            if row["status"] != "pending_review":
                raise HTTPException(status_code=403, detail="运维人员只能维护待审核知识候选；已发布或已下线知识需管理员处理")
            payload["status"] = "pending_review"
        next_version = int(row["version"] or 1) + 1
        conn.execute(
            """
            update knowledge
            set title=?,content=?,tags=?,source_type=?,status=?,version=?,reviewed_by=null,reviewed_at=null,review_note='',updated_at=?
            where id=?
            """,
            (payload["title"], payload["content"], payload["tags"], payload["source_type"], payload["status"], next_version, now, item_id),
        )
    audit("knowledge_update", "knowledge", f"{user.get('real_name','')}更新知识：{payload['title']}，状态：{payload['status']}", item_id)
    return {"id": item_id, **payload, "review_note": "", "reviewed_at": None, "reviewed_by": None, "updated_at": now, "version": next_version}


@app.post("/api/knowledge/{item_id}/status")
def change_knowledge_status(
    item_id: int,
    data: KnowledgeStatusUpdate,
    user: dict[str, Any] = Depends(current_user),
) -> dict[str, Any]:
    require_roles(user, {"admin"})
    validate_knowledge_payload(data)
    now = utc_now()
    with connect() as conn:
        row = conn.execute("select title,status from knowledge where id=?", (item_id,)).fetchone()
        ensure_row_exists(row, "知识条目")
        if row["status"] == data.status:
            return {"id": item_id, "status": data.status, "updated_at": now}
        review_note = data.review_note.strip() or (
            "审核通过并发布" if data.status == "published" else
            "退回待审核" if data.status == "pending_review" else
            "管理员下线"
        )
        conn.execute(
            "update knowledge set status=?,reviewed_by=?,reviewed_at=?,review_note=?,updated_at=? where id=?",
            (data.status, user["id"], now, review_note, now, item_id),
        )
    audit("knowledge_status", "knowledge", f"知识状态变更：{row['title']} {row['status']} -> {data.status}，审核意见：{review_note}", item_id)
    return {"id": item_id, "review_note": review_note, "reviewed_at": now, "reviewed_by": user["id"], "status": data.status, "updated_at": now}


@app.get("/api/issues")
def list_issues(status: str = "", q: str = "", user: dict[str, Any] = Depends(current_user)) -> list[dict[str, Any]]:
    with connect() as conn:
        params: list[Any] = []
        where: list[str] = []
        if status:
            validate_issue_status(status)
            where.append("i.status=?")
            params.append(status)
        if q:
            where.append("(i.title like ? or i.description like ? or i.category like ? or i.impact_scope like ?)")
            params.extend([f"%{q}%", f"%{q}%", f"%{q}%", f"%{q}%"])
        if user.get("role") == "user":
            where.append("i.created_by=?")
            params.append(user["id"])
        where_sql = f"where {' and '.join(where)}" if where else ""
        rows = conn.execute(
            f"""
            select
              i.*,
              u.real_name as created_by_name,
              hu.real_name as handled_by_name,
              vu.real_name as visited_by_name
            from issues i
            left join users u on u.id = i.created_by
            left join users hu on hu.id = i.handled_by
            left join users vu on vu.id = i.visited_by
            {where_sql}
            order by i.id desc
            """,
            params,
        ).fetchall()
        issues = [enrich_issue_item(item) for item in rows_to_dicts(rows)]
        for item in issues:
            event_rows = conn.execute(
                "select event_type,operator_name,content,created_at from issue_events where issue_id=? order by id desc limit 6",
                (item["id"],),
            ).fetchall()
            item["events"] = rows_to_dicts(event_rows)
    return issues


@app.post("/api/issues")
def create_issue(data: IssueCreate, user: dict[str, Any] = Depends(current_user)) -> dict[str, Any]:
    now = utc_now()
    with connect() as conn:
        validate_issue_attachment_refs(conn, data.attachment_url, user)
        cur = conn.execute(
            """
            insert into issues(
              title,description,contact_phone,priority,status,created_at,updated_at,
              created_by,requester_name,category,impact_scope,attachment_url,log_excerpt
            ) values(?,?,?,?,?,?,?,?,?,?,?,?,?)
            """,
            (
                data.title,
                data.description,
                data.contact_phone,
                data.priority,
                "submitted",
                now,
                now,
                user["id"],
                user.get("real_name", ""),
                data.category,
                data.impact_scope,
                data.attachment_url,
                data.log_excerpt,
            ),
        )
        issue_id = int(cur.lastrowid)
        bind_issue_attachments(conn, issue_id, data.attachment_url, user)
        issue_event(conn, issue_id, "created", user, f"创建在线记录：{data.title}")
    audit("issue_create", "issue", f"创建在线记录：{data.title}", issue_id)
    return {"id": issue_id, **data.model_dump(), "status": "submitted", "status_label": ISSUE_STATUS_LABELS["submitted"], "created_at": now, "updated_at": now}


@app.post("/api/issues/{issue_id}/accept")
def accept_issue(issue_id: int, user: dict[str, Any] = Depends(current_user)) -> dict[str, Any]:
    require_roles(user, {"admin", "ops"})
    now = utc_now()
    with connect() as conn:
        row = conn.execute("select id,status,accepted_at from issues where id=?", (issue_id,)).fetchone()
        ensure_row_exists(row, "在线记录")
        if row["status"] == "closed":
            raise HTTPException(status_code=400, detail="已关闭记录不能重新受理")
        accepted_at = row["accepted_at"] or now
        conn.execute(
            "update issues set status='accepted',handled_by=?,accepted_at=?,updated_at=? where id=?",
            (user["id"], accepted_at, now, issue_id),
        )
        issue_event(conn, issue_id, "accepted", user, "已受理在线记录")
    audit("issue_accept", "issue", f"{user.get('real_name','')}受理在线记录 #{issue_id}", issue_id)
    return {"id": issue_id, "status": "accepted", "status_label": ISSUE_STATUS_LABELS["accepted"], "accepted_at": accepted_at}


@app.post("/api/issues/{issue_id}/status")
def change_issue_status(
    issue_id: int,
    data: IssueStatusUpdate,
    user: dict[str, Any] = Depends(current_user),
) -> dict[str, Any]:
    require_roles(user, {"admin", "ops"})
    validate_issue_status(data.status, ISSUE_OPERATOR_STATUSES)
    now = utc_now()
    with connect() as conn:
        row = conn.execute("select id,status,accepted_at from issues where id=?", (issue_id,)).fetchone()
        ensure_row_exists(row, "在线记录")
        if row["status"] == "closed":
            raise HTTPException(status_code=400, detail="已关闭记录不能变更状态")
        accepted_at = row["accepted_at"] or now
        conn.execute(
            "update issues set status=?,handled_by=?,accepted_at=?,updated_at=? where id=?",
            (data.status, user["id"], accepted_at, now, issue_id),
        )
        note = data.note.strip() or f"状态变更为：{ISSUE_STATUS_LABELS[data.status]}"
        issue_event(conn, issue_id, "status_changed", user, note)
    audit("issue_status", "issue", f"在线记录 #{issue_id} 状态变更为 {data.status}：{data.note[:80]}", issue_id)
    return {"id": issue_id, "status": data.status, "status_label": ISSUE_STATUS_LABELS[data.status], "updated_at": now}


@app.post("/api/issues/{issue_id}/handle")
def handle_issue(issue_id: int, data: IssueHandle, user: dict[str, Any] = Depends(current_user)) -> dict[str, Any]:
    require_roles(user, {"admin", "ops"})
    now = utc_now()
    with connect() as conn:
        row = conn.execute("select id,status,accepted_at from issues where id=?", (issue_id,)).fetchone()
        ensure_row_exists(row, "在线记录")
        if row["status"] == "closed":
            raise HTTPException(status_code=400, detail="已关闭记录不能提交处理")
        accepted_at = row["accepted_at"] or now
        cur = conn.execute(
            "update issues set solution=?,status='pending_visit',handled_by=?,accepted_at=?,handled_at=?,updated_at=? where id=?",
            (data.solution, user["id"], accepted_at, now, now, issue_id),
        )
        if cur.rowcount != 1:
            raise HTTPException(status_code=404, detail="在线记录不存在")
        issue_event(conn, issue_id, "handled", user, data.solution)
    audit("issue_handle", "issue", f"处理在线记录：{data.solution[:80]}", issue_id)
    return {"id": issue_id, "status": "pending_visit", "status_label": ISSUE_STATUS_LABELS["pending_visit"], "solution": data.solution, "handled_at": now}


@app.post("/api/issues/{issue_id}/visit")
def visit_issue(issue_id: int, data: IssueVisit, user: dict[str, Any] = Depends(current_user)) -> dict[str, Any]:
    require_roles(user, {"admin", "ops"})
    now = utc_now()
    status = "closed" if data.resolved else "need_user_info"
    closed_at = now if data.resolved else ""
    with connect() as conn:
        row = conn.execute("select id,title,solution from issues where id=?", (issue_id,)).fetchone()
        ensure_row_exists(row, "在线记录")
        cur = conn.execute(
            "update issues set resolved=?,satisfaction_score=?,visit_result=?,status=?,visited_by=?,closed_at=?,updated_at=? where id=?",
            (int(data.resolved), data.satisfaction_score, data.visit_result, status, user["id"], closed_at, now, issue_id),
        )
        if cur.rowcount != 1:
            raise HTTPException(status_code=404, detail="在线记录不存在")
        issue_event(conn, issue_id, "visited", user, f"{'已解决' if data.resolved else '未解决'}：{data.visit_result}")
        if data.resolved and row and row["solution"]:
            conn.execute(
                "insert into knowledge(title,content,tags,source_type,status,created_at,updated_at) values(?,?,?,?,?,?,?)",
                (f"处理案例：{row['title']}", row["solution"], "处理案例,回访已解决,待审核", "case", "pending_review", now, now),
            )
            issue_event(conn, issue_id, "knowledge_candidate", user, "回访确认已解决，处理结果已生成待审核知识候选")
    audit("issue_visit", "issue", f"回访：{'已解决' if data.resolved else '未解决'} {data.visit_result}", issue_id)
    return {"id": issue_id, "status": status, "status_label": ISSUE_STATUS_LABELS[status], "resolved": data.resolved}


@app.post("/api/issues/{issue_id}/knowledge-candidate")
def create_issue_knowledge_candidate(issue_id: int, user: dict[str, Any] = Depends(current_user)) -> dict[str, Any]:
    require_roles(user, {"admin", "ops"})
    now = utc_now()
    with connect() as conn:
        row = conn.execute("select title,description,solution,category,log_excerpt from issues where id=?", (issue_id,)).fetchone()
        ensure_row_exists(row, "在线记录")
        content_parts = [f"问题现象：{row['description']}"]
        if row["log_excerpt"]:
            content_parts.append(f"日志摘要：{row['log_excerpt']}")
        if row["solution"]:
            content_parts.append(f"处理结果：{row['solution']}")
        else:
            content_parts.append("处理结果：待补充，请审核人员根据最终处理结果完善。")
        cur = conn.execute(
            "insert into knowledge(title,content,tags,source_type,status,created_at,updated_at) values(?,?,?,?,?,?,?)",
            (
                f"知识候选：{row['title']}",
                "\n\n".join(content_parts),
                f"{row['category'] or 'general'},处理案例,待审核",
                "case",
                "pending_review",
                now,
                now,
            ),
        )
        knowledge_id = int(cur.lastrowid)
        issue_event(conn, issue_id, "knowledge_candidate", user, f"已生成待审核知识候选 #{knowledge_id}")
    audit("knowledge_candidate_create", "knowledge", f"从在线记录生成知识候选：{row['title']}", knowledge_id)
    return {"id": knowledge_id, "status": "pending_review", "title": f"知识候选：{row['title']}"}


@app.post("/api/issues/{issue_id}/feedback")
def feedback_issue(issue_id: int, data: IssueFeedback, user: dict[str, Any] = Depends(current_user)) -> dict[str, Any]:
    if user.get("role") not in {"user", "admin", "ops"}:
        raise HTTPException(status_code=403, detail="Permission denied")
    now = utc_now()
    with connect() as conn:
        row = conn.execute("select id,created_by,status from issues where id=?", (issue_id,)).fetchone()
        ensure_row_exists(row, "在线记录")
        if user.get("role") == "user" and row["created_by"] != user["id"]:
            raise HTTPException(status_code=403, detail="只能评价自己提交的在线记录")
        if row["status"] != "closed":
            raise HTTPException(status_code=400, detail="在线记录关闭后才能评价")
        conn.execute(
            "update issues set user_satisfaction_score=?,user_feedback=?,updated_at=? where id=?",
            (data.satisfaction_score, data.feedback, now, issue_id),
        )
        issue_event(conn, issue_id, "user_feedback", user, f"用户评价 {data.satisfaction_score} 分：{data.feedback}")
    audit("issue_feedback", "issue", f"用户满意度评价：{data.satisfaction_score} {data.feedback[:80]}", issue_id)
    return {"id": issue_id, "user_satisfaction_score": data.satisfaction_score, "user_feedback": data.feedback}


@app.get("/api/issues/{issue_id}/assist")
def assist_issue(issue_id: int, user: dict[str, Any] = Depends(current_user)) -> dict[str, Any]:
    require_roles(user, {"admin", "ops"})
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
    retrieval = rag_service.search(
        f"{issue.get('title', '')} {issue.get('description', '')} {issue.get('category', '')} {issue.get('log_excerpt', '')}",
        limit=5,
    )
    assist = build_issue_assist(issue, retrieval.references)
    audit("issue_assist", "issue", f"生成处理辅助：{issue.get('title')}", issue_id)
    return assist


@app.get("/api/accounts")
def list_accounts(q: str = "", user: dict[str, Any] = Depends(current_user)) -> list[dict[str, Any]]:
    require_roles(user, {"admin", "ops", "auditor"})
    with connect() as conn:
        rows = fetch_account_rows(conn, q)
    return [account_row_to_dict(row) for row in rows]


@app.get("/api/accounts/export")
def export_accounts(q: str = "", user: dict[str, Any] = Depends(current_user)) -> dict[str, Any]:
    require_roles(user, {"admin", "auditor"})
    with connect() as conn:
        accounts = [account_row_to_dict(row) for row in fetch_account_rows(conn, q)]
    audit("account_export", "ops_account", f"导出运维账号 CSV：{len(accounts)} 条，查询条件：{q or '全部'}")
    return {
        "content": build_accounts_csv(accounts),
        "count": len(accounts),
        "filename": f"ops_accounts_{datetime.utcnow().strftime('%Y%m%d%H%M%S')}.csv",
    }


@app.post("/api/accounts")
def create_account(data: AccountCreate, user: dict[str, Any] = Depends(current_user)) -> dict[str, Any]:
    require_roles(user, {"admin"})
    validate_account_create(data)
    now = utc_now()
    with connect() as conn:
        existing = conn.execute("select id from ops_accounts where account_name=?", (data.account_name,)).fetchone()
        if existing:
            raise HTTPException(status_code=409, detail="账号名已存在")
        cur = conn.execute(
            """
            insert into ops_accounts(
              account_name,owner_name,department,contact_phone,permission_scope,status,risk_level,expires_at,remark,created_at,updated_at
            ) values(?,?,?,?,?,?,?,?,?,?,?)
            """,
            (
                data.account_name,
                data.owner_name,
                data.department,
                data.contact_phone,
                data.permission_scope,
                "active",
                data.risk_level,
                data.expires_at,
                data.remark,
                now,
                now,
            ),
        )
        account_id = int(cur.lastrowid)
    audit("account_create", "ops_account", f"新增运维账号：{data.account_name}", account_id)
    return {"id": account_id, **data.model_dump(), "status": "active", "created_at": now, "updated_at": now}


@app.put("/api/accounts/{account_id}")
def update_account(account_id: int, data: AccountUpdate, user: dict[str, Any] = Depends(current_user)) -> dict[str, Any]:
    require_roles(user, {"admin", "ops"})
    fields = {k: v for k, v in data.model_dump().items() if v is not None}
    fields = normalize_account_payload("update", fields)
    if not fields:
        return {"id": account_id}
    with connect() as conn:
        approval_id = create_account_approval_record(conn, account_id, "update", fields, "申请修改运维账号信息", user)
    return {"id": account_id, "approval_id": approval_id, "status": "pending_approval"}


@app.post("/api/accounts/{account_id}/freeze")
def freeze_account(account_id: int, user: dict[str, Any] = Depends(current_user)) -> dict[str, Any]:
    require_roles(user, {"admin", "ops"})
    with connect() as conn:
        row = conn.execute("select status from ops_accounts where id=?", (account_id,)).fetchone()
        ensure_row_exists(row, "运维账号")
        if row["status"] == "frozen":
            raise HTTPException(status_code=400, detail="账号已冻结")
        approval_id = create_account_approval_record(conn, account_id, "freeze", {}, "申请冻结运维账号", user)
    return {"id": account_id, "approval_id": approval_id, "status": "pending_approval"}


@app.post("/api/accounts/{account_id}/unfreeze")
def unfreeze_account(account_id: int, user: dict[str, Any] = Depends(current_user)) -> dict[str, Any]:
    require_roles(user, {"admin", "ops"})
    with connect() as conn:
        row = conn.execute("select status from ops_accounts where id=?", (account_id,)).fetchone()
        ensure_row_exists(row, "运维账号")
        if row["status"] == "active":
            raise HTTPException(status_code=400, detail="账号已是启用状态")
        approval_id = create_account_approval_record(conn, account_id, "unfreeze", {}, "申请解冻运维账号", user)
    return {"id": account_id, "approval_id": approval_id, "status": "pending_approval"}


@app.get("/api/account-approvals")
def list_account_approvals(status: str = "", user: dict[str, Any] = Depends(current_user)) -> list[dict[str, Any]]:
    require_roles(user, {"admin", "ops", "auditor"})
    with connect() as conn:
        params: list[Any] = []
        where = ""
        if status:
            where = "where aa.status=?"
            params.append(status)
        rows = conn.execute(
            f"""
            select aa.*, oa.account_name, ru.real_name as requester_name, au.real_name as approver_name
            from account_approvals aa
            left join ops_accounts oa on oa.id = aa.account_id
            left join users ru on ru.id = aa.requested_by
            left join users au on au.id = aa.approved_by
            {where}
            order by aa.id desc
            """,
            params,
        ).fetchall()
    return rows_to_dicts(rows)


@app.post("/api/account-approvals")
def create_account_approval(data: AccountApprovalCreate, user: dict[str, Any] = Depends(current_user)) -> dict[str, Any]:
    require_roles(user, {"admin", "ops"})
    with connect() as conn:
        approval_id = create_account_approval_record(conn, data.account_id, data.action, data.payload, data.reason, user)
    return {"id": approval_id, "status": "pending"}


@app.post("/api/account-approvals/{approval_id}/decision")
def decide_account_approval(
    approval_id: int,
    data: AccountApprovalDecision,
    user: dict[str, Any] = Depends(current_user),
) -> dict[str, Any]:
    require_roles(user, {"admin"})
    now = utc_now()
    with connect() as conn:
        row = conn.execute("select * from account_approvals where id=?", (approval_id,)).fetchone()
        ensure_row_exists(row, "账号审批")
        approval = dict(row)
        if approval["status"] != "pending":
            raise HTTPException(status_code=400, detail="审批已处理")
        if data.decision == "approved":
            apply_account_action(conn, approval, user)
        conn.execute(
            "update account_approvals set status=?,decision_reason=?,approved_by=?,decided_at=?,updated_at=? where id=?",
            (data.decision, data.reason, user.get("id"), now, now, approval_id),
        )
        write_audit(conn, "account_approval_decision", "account_approval", f"账号审批{data.decision}：{approval['action']} account={approval['account_id']}", approval_id)
    return {"id": approval_id, "status": data.decision}


@app.get("/api/audit/logs")
def audit_logs(
    limit: int = Query(100, le=500),
    event_type: str = "",
    target_type: str = "",
    q: str = "",
    need_human: str = "",
    user: dict[str, Any] = Depends(current_user),
) -> dict[str, Any]:
    require_roles(user, {"admin", "auditor"})
    with connect() as conn:
        audit_where: list[str] = []
        audit_params: list[Any] = []
        if event_type:
            audit_where.append("event_type=?")
            audit_params.append(event_type)
        if target_type:
            audit_where.append("target_type=?")
            audit_params.append(target_type)
        if q:
            audit_where.append("(content like ? or event_type like ? or target_type like ?)")
            audit_params.extend([f"%{q}%", f"%{q}%", f"%{q}%"])
        audit_where_sql = f"where {' and '.join(audit_where)}" if audit_where else ""
        audit_rows = conn.execute(
            f"select * from audit_logs {audit_where_sql} order by id desc limit ?",
            [*audit_params, limit],
        ).fetchall()

        qa_where: list[str] = []
        qa_params: list[Any] = []
        if q:
            qa_where.append("(question like ? or answer like ? or model_status like ?)")
            qa_params.extend([f"%{q}%", f"%{q}%", f"%{q}%"])
        if need_human in {"0", "1"}:
            qa_where.append("need_human=?")
            qa_params.append(int(need_human))
        qa_where_sql = f"where {' and '.join(qa_where)}" if qa_where else ""
        qa_rows = conn.execute(
            f"select * from qa_logs {qa_where_sql} order by id desc limit ?",
            [*qa_params, limit],
        ).fetchall()

        event_rows = conn.execute(
            "select event_type,count(*) as count from audit_logs group by event_type order by count desc limit 10"
        ).fetchall()
        target_rows = conn.execute(
            "select target_type,count(*) as count from audit_logs group by target_type order by count desc"
        ).fetchall()
    return {
        "audit": rows_to_dicts(audit_rows),
        "qa": rows_to_dicts(qa_rows),
        "event_summary": rows_to_dicts(event_rows),
        "target_summary": rows_to_dicts(target_rows),
    }


@app.get("/api/audit/stats")
def stats(user: dict[str, Any] = Depends(current_user)) -> dict[str, Any]:
    active_statuses = tuple(sorted(ISSUE_ACTIVE_STATUSES))
    pending_visit_statuses = tuple(sorted(ISSUE_PENDING_VISIT_STATUSES))
    with connect() as conn:
        if user.get("role") == "user":
            issues = conn.execute("select count(*) from issues where created_by=?", (user["id"],)).fetchone()[0]
            pending_issues = conn.execute(
                f"select count(*) from issues where created_by=? and status in ({','.join('?' for _ in active_statuses)})",
                (user["id"], *active_statuses),
            ).fetchone()[0]
            handled_issues = conn.execute(
                f"select count(*) from issues where created_by=? and status in ({','.join('?' for _ in pending_visit_statuses)})",
                (user["id"], *pending_visit_statuses),
            ).fetchone()[0]
            closed = conn.execute("select count(*) from issues where created_by=? and status='closed'", (user["id"],)).fetchone()[0]
            total_qa = conn.execute("select count(*) from qa_conversations where user_id=?", (user["id"],)).fetchone()[0]
            return {
                "total_qa": total_qa,
                "human_transfer_rate": 0,
                "self_solved_rate": 0,
                "issues": issues,
                "pending_issues": pending_issues,
                "handled_issues": handled_issues,
                "closed_issues": closed,
            }
        require_roles(user, {"admin", "ops", "auditor"})
        total_qa = conn.execute("select count(*) from qa_logs").fetchone()[0]
        human = conn.execute("select count(*) from qa_logs where need_human=1").fetchone()[0]
        issues = conn.execute("select count(*) from issues").fetchone()[0]
        pending_issues = conn.execute(
            f"select count(*) from issues where status in ({','.join('?' for _ in active_statuses)})",
            active_statuses,
        ).fetchone()[0]
        handled_issues = conn.execute(
            f"select count(*) from issues where status in ({','.join('?' for _ in pending_visit_statuses)})",
            pending_visit_statuses,
        ).fetchone()[0]
        accounts = conn.execute("select count(*) from ops_accounts").fetchone()[0]
        active_accounts = conn.execute("select count(*) from ops_accounts where status='active'").fetchone()[0]
        frozen_accounts = conn.execute("select count(*) from ops_accounts where status='frozen'").fetchone()[0]
        knowledge = conn.execute("select count(*) from knowledge").fetchone()[0]
        published_knowledge = conn.execute("select count(*) from knowledge where status='published'").fetchone()[0]
        pending_knowledge = conn.execute("select count(*) from knowledge where status='pending_review'").fetchone()[0]
        closed = conn.execute("select count(*) from issues where status='closed'").fetchone()[0]
        audit_count = conn.execute("select count(*) from audit_logs").fetchone()[0]
        reference_rows = conn.execute("select references_json from qa_logs").fetchall()
    rag_hit_count = 0
    rag_score_sum = 0.0
    for row in reference_rows:
        try:
            refs = json.loads(row["references_json"] or "[]")
        except json.JSONDecodeError:
            refs = []
        if refs:
            rag_hit_count += 1
            rag_score_sum += float(refs[0].get("score") or 0)
    return {
        "total_qa": total_qa,
        "human_transfer_rate": human / total_qa if total_qa else 0,
        "self_solved_rate": 1 - (human / total_qa) if total_qa else 0,
        "knowledge_hit_rate": rag_hit_count / total_qa if total_qa else 0,
        "average_rag_confidence": rag_score_sum / rag_hit_count if rag_hit_count else 0,
        "issues": issues,
        "pending_issues": pending_issues,
        "handled_issues": handled_issues,
        "accounts": accounts,
        "active_accounts": active_accounts,
        "frozen_accounts": frozen_accounts,
        "knowledge": knowledge,
        "published_knowledge": published_knowledge,
        "pending_knowledge": pending_knowledge,
        "closed_issues": closed,
        "audit_count": audit_count,
    }
