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
    IssueVisit,
    KnowledgeCreate,
    KnowledgeStatusUpdate,
    LoginRequest,
    QuestionRequest,
)
from .security import create_access_token, current_user, verify_password
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
ISSUE_CATEGORY_KEYWORDS = {
    "account": ["账号", "密码", "登录", "权限", "冻结", "解冻", "用户"],
    "network": ["vpn", "网络", "连接", "证书", "远程", "wifi", "专线"],
    "business": ["系统", "应用", "页面", "业务", "接口", "访问慢", "报错"],
    "database": ["数据库", "mysql", "oracle", "redis", "连接池", "sql", "中间件"],
}
HIGH_PRIORITY_KEYWORDS = ["生产", "全公司", "全部", "大面积", "中断", "无法访问", "宕机", "紧急", "批量", "高优先级"]
LOW_PRIORITY_KEYWORDS = ["咨询", "了解", "低优先级", "不紧急"]

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


def validate_knowledge_payload(data: KnowledgeCreate | KnowledgeStatusUpdate) -> None:
    if getattr(data, "status", "") not in KNOWLEDGE_STATUSES:
        raise HTTPException(status_code=400, detail="知识状态只能是 pending_review、published 或 offline")
    source_type = getattr(data, "source_type", None)
    if source_type is not None and source_type not in KNOWLEDGE_SOURCE_TYPES:
        raise HTTPException(status_code=400, detail="知识来源类型不合法")


def build_issue_draft(description: str) -> dict[str, Any]:
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
    }


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
                "content_preview": str(item.get("content", ""))[:180],
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
    result = rag_service.search(data.question)
    context = rag_service.build_context(result.references)
    try:
        model_result = llm_service.generate(data.question, context, data.enable_thinking)
    except RuntimeError as exc:
        raise HTTPException(status_code=503, detail=f"LLM 数字员工不可用：{exc}") from exc
    answer = model_result["content"]
    need_human = result.high_risk or result.confidence < 0.08 or not result.references
    if need_human:
        answer += "\n\n系统判断该问题建议转人工：请创建在线记录，补充影响范围、联系方式和错误截图/日志。"
    refs = [{"id": item["id"], "title": item["title"], "tags": item.get("tags", ""), "score": item.get("score", 0)} for item in result.references]
    with connect() as conn:
        conn.execute(
            "insert into qa_logs(question,answer,need_human,model_status,references_json,created_at) values(?,?,?,?,?,?)",
            (data.question, answer, int(need_human), model_result.get("status", "unknown"), json.dumps(refs, ensure_ascii=False), utc_now()),
        )
    next_actions = [
        {"key": "create_issue", "label": "创建在线记录", "enabled": True},
        {"key": "view_references", "label": "查看引用知识", "enabled": bool(refs)},
    ]
    if not need_human:
        next_actions.insert(0, {"key": "self_check", "label": "按步骤自助处理", "enabled": True})
    return {
        "answer": answer,
        "references": refs,
        "need_human": need_human,
        "model_status": model_result.get("status", "unknown"),
        "llm_used": True,
        "reasoning_enabled": model_result.get("reasoning_enabled", False),
        "reasoning_available": model_result.get("reasoning_available", False),
        "employee": {
            "name": "云维",
            "role": "企业运维数字员工",
            "mode": "llm",
        },
        "next_actions": next_actions,
        "issue_draft": {
            "title": data.question[:40],
            "description": data.question,
            "priority": "high" if need_human else "medium",
        },
    }


@app.get("/api/qa/suggest")
def suggest(q: str = "", limit: int = Query(8, ge=1, le=20), user: dict[str, Any] = Depends(current_user)) -> list[dict[str, Any]]:
    return rag_service.suggest(q, limit)


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
        can_access = user.get("role") in {"admin", "ops", "auditor"} or row["uploaded_by"] == user.get("id")
        if not can_access and user.get("role") == "user":
            pattern = f"%/api/issues/attachments/{attachment_id}/download%"
            issue_row = conn.execute(
                "select id from issues where created_by=? and attachment_url like ?",
                (user.get("id"), pattern),
            ).fetchone()
            can_access = bool(issue_row)
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
        if q:
            where.append("(title like ? or content like ? or tags like ?)")
            params.extend([f"%{q}%", f"%{q}%", f"%{q}%"])
        if status:
            where.append("status=?")
            params.append(status)
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
    now = utc_now()
    with connect() as conn:
        cur = conn.execute(
            "insert into knowledge(title,content,tags,source_type,status,created_at,updated_at) values(?,?,?,?,?,?,?)",
            (data.title, data.content, data.tags, data.source_type, data.status, now, now),
        )
        item_id = int(cur.lastrowid)
    audit("knowledge_create", "knowledge", f"新增知识：{data.title}", item_id)
    return {"id": item_id, **data.model_dump(), "created_at": now, "updated_at": now}


@app.put("/api/knowledge/{item_id}")
def update_knowledge(item_id: int, data: KnowledgeCreate, user: dict[str, Any] = Depends(current_user)) -> dict[str, Any]:
    require_roles(user, {"admin", "ops"})
    validate_knowledge_payload(data)
    now = utc_now()
    with connect() as conn:
        row = conn.execute("select id from knowledge where id=?", (item_id,)).fetchone()
        ensure_row_exists(row, "知识条目")
        conn.execute(
            "update knowledge set title=?,content=?,tags=?,source_type=?,status=?,updated_at=? where id=?",
            (data.title, data.content, data.tags, data.source_type, data.status, now, item_id),
        )
    audit("knowledge_update", "knowledge", f"更新知识：{data.title}", item_id)
    return {"id": item_id, **data.model_dump(), "updated_at": now}


@app.post("/api/knowledge/{item_id}/status")
def change_knowledge_status(
    item_id: int,
    data: KnowledgeStatusUpdate,
    user: dict[str, Any] = Depends(current_user),
) -> dict[str, Any]:
    require_roles(user, {"admin", "ops"})
    validate_knowledge_payload(data)
    now = utc_now()
    with connect() as conn:
        row = conn.execute("select title,status from knowledge where id=?", (item_id,)).fetchone()
        ensure_row_exists(row, "知识条目")
        if row["status"] == data.status:
            return {"id": item_id, "status": data.status, "updated_at": now}
        conn.execute("update knowledge set status=?,updated_at=? where id=?", (data.status, now, item_id))
    audit("knowledge_status", "knowledge", f"知识状态变更：{row['title']} {row['status']} -> {data.status}", item_id)
    return {"id": item_id, "status": data.status, "updated_at": now}


@app.get("/api/issues")
def list_issues(status: str = "", q: str = "", user: dict[str, Any] = Depends(current_user)) -> list[dict[str, Any]]:
    with connect() as conn:
        params: list[Any] = []
        where: list[str] = []
        if status:
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
            select i.*, u.real_name as created_by_name
            from issues i
            left join users u on u.id = i.created_by
            {where_sql}
            order by i.id desc
            """,
            params,
        ).fetchall()
        issues = rows_to_dicts(rows)
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
                "pending",
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
        issue_event(conn, issue_id, "created", user, f"创建在线记录：{data.title}")
    audit("issue_create", "issue", f"创建在线记录：{data.title}", issue_id)
    return {"id": issue_id, **data.model_dump(), "status": "pending", "created_at": now, "updated_at": now}


@app.post("/api/issues/{issue_id}/handle")
def handle_issue(issue_id: int, data: IssueHandle, user: dict[str, Any] = Depends(current_user)) -> dict[str, Any]:
    require_roles(user, {"admin", "ops"})
    now = utc_now()
    with connect() as conn:
        conn.execute(
            "update issues set solution=?,status='handled',handled_by=?,updated_at=? where id=?",
            (data.solution, user["id"], now, issue_id),
        )
        issue_event(conn, issue_id, "handled", user, data.solution)
    audit("issue_handle", "issue", f"处理在线记录：{data.solution[:80]}", issue_id)
    return {"id": issue_id, "status": "handled", "solution": data.solution}


@app.post("/api/issues/{issue_id}/visit")
def visit_issue(issue_id: int, data: IssueVisit, user: dict[str, Any] = Depends(current_user)) -> dict[str, Any]:
    require_roles(user, {"admin", "ops"})
    now = utc_now()
    status = "closed" if data.resolved else "pending"
    with connect() as conn:
        conn.execute(
            "update issues set resolved=?,satisfaction_score=?,visit_result=?,status=?,visited_by=?,updated_at=? where id=?",
            (int(data.resolved), data.satisfaction_score, data.visit_result, status, user["id"], now, issue_id),
        )
        issue_event(conn, issue_id, "visited", user, f"{'已解决' if data.resolved else '未解决'}：{data.visit_result}")
        row = conn.execute("select title,solution from issues where id=?", (issue_id,)).fetchone()
        if data.resolved and row and row["solution"]:
            conn.execute(
                "insert into knowledge(title,content,tags,source_type,status,created_at,updated_at) values(?,?,?,?,?,?,?)",
                (f"处理案例：{row['title']}", row["solution"], "处理案例,回访已解决,待审核", "case", "pending_review", now, now),
            )
            issue_event(conn, issue_id, "knowledge_candidate", user, "回访确认已解决，处理结果已生成待审核知识候选")
    audit("issue_visit", "issue", f"回访：{'已解决' if data.resolved else '未解决'} {data.visit_result}", issue_id)
    return {"id": issue_id, "status": status, "resolved": data.resolved}


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
    with connect() as conn:
        total_qa = conn.execute("select count(*) from qa_logs").fetchone()[0]
        human = conn.execute("select count(*) from qa_logs where need_human=1").fetchone()[0]
        issues = conn.execute("select count(*) from issues").fetchone()[0]
        pending_issues = conn.execute("select count(*) from issues where status='pending'").fetchone()[0]
        handled_issues = conn.execute("select count(*) from issues where status='handled'").fetchone()[0]
        accounts = conn.execute("select count(*) from ops_accounts").fetchone()[0]
        active_accounts = conn.execute("select count(*) from ops_accounts where status='active'").fetchone()[0]
        frozen_accounts = conn.execute("select count(*) from ops_accounts where status='frozen'").fetchone()[0]
        knowledge = conn.execute("select count(*) from knowledge").fetchone()[0]
        published_knowledge = conn.execute("select count(*) from knowledge where status='published'").fetchone()[0]
        pending_knowledge = conn.execute("select count(*) from knowledge where status='pending_review'").fetchone()[0]
        closed = conn.execute("select count(*) from issues where status='closed'").fetchone()[0]
        audit_count = conn.execute("select count(*) from audit_logs").fetchone()[0]
    return {
        "total_qa": total_qa,
        "human_transfer_rate": human / total_qa if total_qa else 0,
        "self_solved_rate": 1 - (human / total_qa) if total_qa else 0,
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
