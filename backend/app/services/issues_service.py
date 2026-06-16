from __future__ import annotations

import re
import uuid
from datetime import datetime
from pathlib import Path
from typing import Any

from fastapi import HTTPException, UploadFile
from fastapi.responses import FileResponse

from ..database import audit, connect, issue_event, rows_to_dicts, utc_now
from ..deps import ensure_row_exists
from ..schemas import IssueCreate, IssueDraftRequest, IssueFeedback, IssueHandle, IssueStatusUpdate, IssueVisit
from .knowledge_service import redact_sensitive_value
from .qa_service import build_issue_draft, rag_service

UPLOAD_DIR = Path("backend/data/uploads")
ALLOWED_UPLOAD_SUFFIXES = {".jpg", ".jpeg", ".png", ".gif", ".webp", ".txt", ".log", ".pdf", ".zip"}
MAX_UPLOAD_BYTES = 10 * 1024 * 1024
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
ISSUE_ACCEPTABLE_STATUSES = {"need_user_info", "pending", "submitted"}
ISSUE_HANDLEABLE_STATUSES = {"accepted", "need_user_info", "processing"}
ISSUE_VISITABLE_STATUSES = {"handled", "pending_visit"}
ALLOWED_ATTACHMENT_SCHEMES = {"http", "https"}
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


def ensure_upload_dir() -> None:
    UPLOAD_DIR.mkdir(parents=True, exist_ok=True)


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


def validate_attachment_url_text(attachment_url: str) -> None:
    for value in re.split(r"[\s,，]+", attachment_url or ""):
        value = value.strip()
        if not value:
            continue
        match = re.match(r"^([A-Za-z][A-Za-z0-9+.-]*):", value)
        if match and match.group(1).lower() not in ALLOWED_ATTACHMENT_SCHEMES:
            raise HTTPException(status_code=400, detail="附件链接协议不支持")


def enrich_issue_item(item: dict[str, Any]) -> dict[str, Any]:
    now = utc_now()
    item["status_label"] = ISSUE_STATUS_LABELS.get(item.get("status", ""), item.get("status", ""))
    item["response_minutes"] = elapsed_minutes(item.get("created_at", ""), item.get("accepted_at", ""))
    item["handling_minutes"] = elapsed_minutes(item.get("accepted_at", ""), item.get("handled_at", ""))
    item["total_minutes"] = elapsed_minutes(item.get("created_at", ""), item.get("closed_at", "") or now)
    return item


def build_issue_assist(issue: dict[str, Any], references: list[dict[str, Any]]) -> dict[str, Any]:
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


def extract_attachment_ids(attachment_url: str) -> list[int]:
    ids = re.findall(r"/api/issues/attachments/(\d+)/download", attachment_url or "")
    return sorted({int(item) for item in ids})


def validate_issue_attachment_refs(conn: Any, attachment_url: str, user: dict[str, Any]) -> None:
    if not attachment_url:
        return
    validate_attachment_url_text(attachment_url)
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


def draft_issue(data: IssueDraftRequest) -> dict[str, Any]:
    draft = build_issue_draft(data.description)
    audit("issue_draft", "issue", f"生成在线记录草稿：{draft['title']}", None)
    return draft


def upload_issue_attachment(file: UploadFile, user: dict[str, Any]) -> dict[str, Any]:
    ensure_upload_dir()
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


def download_issue_attachment(attachment_id: int, user: dict[str, Any]) -> FileResponse:
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


def list_issues(status: str, q: str, user: dict[str, Any]) -> list[dict[str, Any]]:
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


def create_issue(data: IssueCreate, user: dict[str, Any]) -> dict[str, Any]:
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


def accept_issue(issue_id: int, user: dict[str, Any]) -> dict[str, Any]:
    now = utc_now()
    with connect() as conn:
        row = conn.execute("select id,status,accepted_at from issues where id=?", (issue_id,)).fetchone()
        ensure_row_exists(row, "在线记录")
        if row["status"] == "closed":
            raise HTTPException(status_code=400, detail="已关闭记录不能重新受理")
        if row["status"] not in ISSUE_ACCEPTABLE_STATUSES:
            raise HTTPException(status_code=400, detail="只有已提交、待处理或待用户补充的记录可以受理")
        accepted_at = row["accepted_at"] or now
        conn.execute(
            "update issues set status='accepted',handled_by=?,accepted_at=?,updated_at=? where id=?",
            (user["id"], accepted_at, now, issue_id),
        )
        issue_event(conn, issue_id, "accepted", user, "已受理在线记录")
    audit("issue_accept", "issue", f"{user.get('real_name','')}受理在线记录 #{issue_id}", issue_id)
    return {"id": issue_id, "status": "accepted", "status_label": ISSUE_STATUS_LABELS["accepted"], "accepted_at": accepted_at}


def change_issue_status(issue_id: int, data: IssueStatusUpdate, user: dict[str, Any]) -> dict[str, Any]:
    validate_issue_status(data.status, ISSUE_OPERATOR_STATUSES)
    now = utc_now()
    with connect() as conn:
        row = conn.execute("select id,status,accepted_at from issues where id=?", (issue_id,)).fetchone()
        ensure_row_exists(row, "在线记录")
        if row["status"] == "closed":
            raise HTTPException(status_code=400, detail="已关闭记录不能变更状态")
        if row["status"] in ISSUE_VISITABLE_STATUSES:
            raise HTTPException(status_code=400, detail="待回访记录请通过回访结果继续流转")
        accepted_at = row["accepted_at"] or now
        conn.execute(
            "update issues set status=?,handled_by=?,accepted_at=?,updated_at=? where id=?",
            (data.status, user["id"], accepted_at, now, issue_id),
        )
        note = data.note.strip() or f"状态变更为：{ISSUE_STATUS_LABELS[data.status]}"
        issue_event(conn, issue_id, "status_changed", user, note)
    audit("issue_status", "issue", f"在线记录 #{issue_id} 状态变更为 {data.status}：{data.note[:80]}", issue_id)
    return {"id": issue_id, "status": data.status, "status_label": ISSUE_STATUS_LABELS[data.status], "updated_at": now}


def handle_issue(issue_id: int, data: IssueHandle, user: dict[str, Any]) -> dict[str, Any]:
    now = utc_now()
    with connect() as conn:
        row = conn.execute("select id,status,accepted_at from issues where id=?", (issue_id,)).fetchone()
        ensure_row_exists(row, "在线记录")
        if row["status"] == "closed":
            raise HTTPException(status_code=400, detail="已关闭记录不能提交处理")
        if row["status"] not in ISSUE_HANDLEABLE_STATUSES:
            raise HTTPException(status_code=400, detail="只有已受理、处理中或待用户补充的记录可以提交处理")
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


def visit_issue(issue_id: int, data: IssueVisit, user: dict[str, Any]) -> dict[str, Any]:
    now = utc_now()
    status = "closed" if data.resolved else "need_user_info"
    closed_at = now if data.resolved else ""
    with connect() as conn:
        row = conn.execute("select id,title,solution,status from issues where id=?", (issue_id,)).fetchone()
        ensure_row_exists(row, "在线记录")
        if row["status"] not in ISSUE_VISITABLE_STATUSES:
            raise HTTPException(status_code=400, detail="只有待回访记录可以回访确认")
        if data.resolved and not row["solution"]:
            raise HTTPException(status_code=400, detail="缺少处理结果，不能关闭记录")
        cur = conn.execute(
            "update issues set resolved=?,satisfaction_score=?,visit_result=?,status=?,visited_by=?,closed_at=?,updated_at=? where id=?",
            (int(data.resolved), data.satisfaction_score, data.visit_result, status, user["id"], closed_at, now, issue_id),
        )
        if cur.rowcount != 1:
            raise HTTPException(status_code=404, detail="在线记录不存在")
        issue_event(conn, issue_id, "visited", user, f"{'已解决' if data.resolved else '未解决'}：{data.visit_result}")
        if data.resolved and row and row["solution"]:
            title = redact_sensitive_value(f"处理案例：{row['title']}")
            content = redact_sensitive_value(row["solution"])
            conn.execute(
                "insert into knowledge(title,content,tags,source_type,status,created_at,updated_at) values(?,?,?,?,?,?,?)",
                (title, content, "处理案例,回访已解决,待审核", "case", "pending_review", now, now),
            )
            issue_event(conn, issue_id, "knowledge_candidate", user, "回访确认已解决，处理结果已生成待审核知识候选")
    audit("issue_visit", "issue", f"回访：{'已解决' if data.resolved else '未解决'} {data.visit_result}", issue_id)
    return {"id": issue_id, "status": status, "status_label": ISSUE_STATUS_LABELS[status], "resolved": data.resolved}


def create_issue_knowledge_candidate(issue_id: int, user: dict[str, Any]) -> dict[str, Any]:
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
        title = redact_sensitive_value(f"知识候选：{row['title']}")
        content = redact_sensitive_value("\n\n".join(content_parts))
        cur = conn.execute(
            "insert into knowledge(title,content,tags,source_type,status,created_at,updated_at) values(?,?,?,?,?,?,?)",
            (
                title,
                content,
                f"{row['category'] or 'general'},处理案例,待审核",
                "case",
                "pending_review",
                now,
                now,
            ),
        )
        knowledge_id = int(cur.lastrowid)
        issue_event(conn, issue_id, "knowledge_candidate", user, f"已生成待审核知识候选 #{knowledge_id}")
    audit("knowledge_candidate_create", "knowledge", f"从在线记录生成知识候选：{title}", knowledge_id)
    return {"id": knowledge_id, "status": "pending_review", "title": title}


def feedback_issue(issue_id: int, data: IssueFeedback, user: dict[str, Any]) -> dict[str, Any]:
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


def assist_issue(issue_id: int) -> dict[str, Any]:
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
