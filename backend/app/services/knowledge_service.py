from __future__ import annotations

import re
from pathlib import Path
from typing import Any

from fastapi import HTTPException

from ..database import audit, connect, rows_to_dicts, utc_now
from ..deps import ensure_row_exists
from ..schemas import KnowledgeCreate, KnowledgeStatusUpdate
from .rag_service import rag_service

KNOWLEDGE_STATUSES = {"pending_review", "published", "offline"}
KNOWLEDGE_SOURCE_TYPES = {"case", "document", "faq", "other", "policy", "runbook"}
DOCUMENT_ALLOWED_EXTENSIONS = {".csv", ".log", ".markdown", ".md", ".txt"}
DOCUMENT_ALLOWED_CONTENT_TYPES = {
    "",
    "application/octet-stream",
    "text/csv",
    "text/markdown",
    "text/plain",
    "text/x-log",
}
DOCUMENT_CHUNK_MAX_CHARS = 1200
DOCUMENT_MAX_BYTES = 1024 * 1024
SENSITIVE_PATTERNS = [
    {
        "key": "mainland_phone",
        "label": "手机号",
        "pattern": re.compile(r"(?<!\d)1[3-9]\d{9}(?!\d)"),
        "replacement": "[手机号已脱敏]",
        "severity": "high",
    },
    {
        "key": "email",
        "label": "邮箱地址",
        "pattern": re.compile(r"\b[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}\b"),
        "replacement": "[邮箱已脱敏]",
        "severity": "medium",
    },
    {
        "key": "id_card",
        "label": "身份证号",
        "pattern": re.compile(r"(?<!\d)\d{17}[\dXx](?!\d)"),
        "replacement": "[证件号已脱敏]",
        "severity": "high",
    },
    {
        "key": "credential",
        "label": "密码/密钥字段",
        "pattern": re.compile(
            r"(?i)\b(password|passwd|pwd|secret|token|api[_-]?key|access[_-]?key)\s*[:=]\s*['\"]?[^'\"\s,;，；]+"
        ),
        "replacement": "[敏感凭据已脱敏]",
        "severity": "high",
    },
    {
        "key": "private_key",
        "label": "私钥内容",
        "pattern": re.compile(r"-----BEGIN [A-Z ]*PRIVATE KEY-----[\s\S]*?-----END [A-Z ]*PRIVATE KEY-----"),
        "replacement": "[私钥已脱敏]",
        "severity": "high",
    },
]
BLOCKING_SENSITIVE_SEVERITIES = {"high"}


def validate_knowledge_payload(data: KnowledgeCreate | KnowledgeStatusUpdate) -> None:
    if getattr(data, "status", "") not in KNOWLEDGE_STATUSES:
        raise HTTPException(status_code=400, detail="知识状态只能是 pending_review、published 或 offline")
    source_type = getattr(data, "source_type", None)
    if source_type is not None and source_type not in KNOWLEDGE_SOURCE_TYPES:
        raise HTTPException(status_code=400, detail="知识来源类型不合法")


def mask_sensitive_sample(value: str) -> str:
    cleaned = value.strip()
    if len(cleaned) <= 4:
        return "***"
    if len(cleaned) <= 8:
        return f"{cleaned[:1]}***{cleaned[-1:]}"
    return f"{cleaned[:3]}***{cleaned[-3:]}"


def redact_sensitive_value(value: str) -> str:
    redacted = value or ""
    for item in SENSITIVE_PATTERNS:
        redacted = item["pattern"].sub(str(item["replacement"]), redacted)
    return redacted


def scan_knowledge_sensitive(title: str = "", content: str = "", tags: str = "") -> dict[str, Any]:
    text = "\n".join([title or "", content or "", tags or ""])
    findings = []
    for item in SENSITIVE_PATTERNS:
        matches = [match.group(0) for match in item["pattern"].finditer(text)]
        if not matches:
            continue
        unique_samples = []
        for match in matches:
            masked = mask_sensitive_sample(match)
            if masked not in unique_samples:
                unique_samples.append(masked)
        findings.append(
            {
                "type": item["key"],
                "label": item["label"],
                "severity": item["severity"],
                "count": len(matches),
                "samples": unique_samples[:3],
                "suggestion": "发布前请脱敏或替换为受控流程说明，避免敏感信息进入 RAG 检索。",
            }
        )
    blocking = any(item["severity"] in BLOCKING_SENSITIVE_SEVERITIES for item in findings)
    return {
        "blocking": blocking,
        "findings": findings,
        "has_sensitive": bool(findings),
        "redacted": {
            "title": redact_sensitive_value(title),
            "content": redact_sensitive_value(content),
            "tags": redact_sensitive_value(tags),
        },
    }


def ensure_knowledge_publishable(title: str, content: str, tags: str = "") -> dict[str, Any]:
    check = scan_knowledge_sensitive(title, content, tags)
    if check["blocking"]:
        labels = "、".join(item["label"] for item in check["findings"] if item["severity"] in BLOCKING_SENSITIVE_SEVERITIES)
        raise HTTPException(
            status_code=400,
            detail={
                "message": f"知识条目包含高风险敏感信息：{labels}。请先脱敏后再发布。",
                "sensitive_check": check,
            },
        )
    return check


def sensitive_summary(check: dict[str, Any]) -> dict[str, Any]:
    return {
        "blocking": check["blocking"],
        "findings": check["findings"],
        "has_sensitive": check["has_sensitive"],
    }


def attach_knowledge_sensitive_check(item: dict[str, Any]) -> dict[str, Any]:
    check = scan_knowledge_sensitive(item.get("title", ""), item.get("content", ""), item.get("tags", ""))
    item["sensitive_check"] = sensitive_summary(check)
    return item


def validate_document_upload(original_name: str, content_type: str, raw: bytes) -> None:
    suffix = Path(original_name or "").suffix.lower()
    if suffix not in DOCUMENT_ALLOWED_EXTENSIONS:
        raise HTTPException(status_code=400, detail="当前仅支持上传 txt、md、markdown、log、csv 纯文本文件")
    if content_type and content_type.split(";", 1)[0].strip().lower() not in DOCUMENT_ALLOWED_CONTENT_TYPES:
        raise HTTPException(status_code=400, detail="当前仅支持上传纯文本类型文件")
    if not raw:
        raise HTTPException(status_code=400, detail="上传文档不能为空")
    if len(raw) > DOCUMENT_MAX_BYTES:
        raise HTTPException(status_code=413, detail="上传文档不能超过 1MB")
    if b"\x00" in raw:
        raise HTTPException(status_code=400, detail="上传文档疑似二进制文件，请转换为纯文本后再导入")


def decode_document_text(raw: bytes) -> str:
    for encoding in ("utf-8-sig", "utf-8", "gb18030"):
        try:
            return raw.decode(encoding)
        except UnicodeDecodeError:
            continue
    raise HTTPException(status_code=400, detail="无法识别文档编码，请使用 UTF-8 文本文件")


def normalize_document_text(text: str) -> str:
    normalized = text.replace("\r\n", "\n").replace("\r", "\n")
    normalized = re.sub(r"[ \t]+$", "", normalized, flags=re.MULTILINE)
    normalized = re.sub(r"\n{4,}", "\n\n\n", normalized)
    return normalized.strip()


def split_long_block(block: str, max_chars: int = DOCUMENT_CHUNK_MAX_CHARS) -> list[str]:
    block = block.strip()
    if len(block) <= max_chars:
        return [block] if block else []
    parts = [item.strip() for item in re.split(r"(?<=[。！？；;.!?])\s*", block) if item.strip()]
    if len(parts) <= 1:
        return [block[index : index + max_chars].strip() for index in range(0, len(block), max_chars) if block[index : index + max_chars].strip()]
    chunks: list[str] = []
    current = ""
    for part in parts:
        if not current:
            current = part
        elif len(current) + len(part) + 1 <= max_chars:
            current = f"{current}\n{part}"
        else:
            chunks.append(current)
            current = part
    if current:
        chunks.append(current)
    return chunks


def split_document_text(text: str, max_chars: int = DOCUMENT_CHUNK_MAX_CHARS) -> list[str]:
    normalized = normalize_document_text(text)
    if not normalized:
        raise HTTPException(status_code=400, detail="上传文档没有可导入的文本内容")
    blocks = [item.strip() for item in re.split(r"\n{2,}", normalized) if item.strip()]
    chunks: list[str] = []
    current = ""
    for block in blocks:
        for part in split_long_block(block, max_chars):
            if not current:
                current = part
            elif len(current) + len(part) + 2 <= max_chars:
                current = f"{current}\n\n{part}"
            else:
                chunks.append(current)
                current = part
    if current:
        chunks.append(current)
    return chunks


def build_document_tags(tags: str, original_name: str) -> str:
    values = ["文档导入"]
    values.extend(tag.strip() for tag in tags.split(",") if tag.strip())
    stem = Path(original_name or "").stem.strip()
    if stem:
        values.append(stem)
    unique: list[str] = []
    for value in values:
        if value and value not in unique:
            unique.append(value)
    return ",".join(unique)


def import_knowledge_document(
    *,
    original_name: str,
    content_type: str,
    raw: bytes,
    title: str,
    tags: str,
    user: dict[str, Any],
) -> dict[str, Any]:
    validate_document_upload(original_name, content_type, raw)
    text = decode_document_text(raw)
    chunks = split_document_text(text)
    base_title = title.strip() or Path(original_name or "").stem.strip() or "上传文档"
    tag_text = build_document_tags(tags, original_name)
    now = utc_now()
    imported_chunks: list[dict[str, Any]] = []

    with connect() as conn:
        for index, chunk in enumerate(chunks, start=1):
            chunk_title = f"{base_title}（片段 {index}/{len(chunks)}）" if len(chunks) > 1 else base_title
            original_check = scan_knowledge_sensitive(chunk_title, chunk, tag_text)
            redacted = original_check["redacted"]
            cur = conn.execute(
                """
                insert into knowledge(
                  title,content,tags,source_type,status,version,reviewed_by,reviewed_at,review_note,created_at,updated_at
                ) values(?,?,?,?,?,?,?,?,?,?,?)
                """,
                (
                    redacted["title"],
                    redacted["content"],
                    redacted["tags"],
                    "document",
                    "pending_review",
                    1,
                    None,
                    None,
                    f"由文档 {original_name} 导入，等待审核发布",
                    now,
                    now,
                ),
            )
            imported_chunks.append(
                {
                    "id": int(cur.lastrowid),
                    "redacted": bool(original_check["has_sensitive"]),
                    "sensitive_check": sensitive_summary(original_check),
                    "source_type": "document",
                    "status": "pending_review",
                    "title": redacted["title"],
                }
            )

    redacted_count = sum(1 for chunk in imported_chunks if chunk["redacted"])
    audit(
        "knowledge_document_import",
        "knowledge",
        f"{user.get('real_name','')}导入文档知识：{original_name}，生成 {len(imported_chunks)} 个候选片段，脱敏 {redacted_count} 个片段",
    )
    rag_service.clear_cache()
    return {
        "chunk_count": len(imported_chunks),
        "chunks": imported_chunks,
        "filename": original_name,
        "redacted_count": redacted_count,
        "status": "pending_review",
    }


def list_knowledge(q: str, status: str, source_type: str, user: dict[str, Any]) -> list[dict[str, Any]]:
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
    items = rows_to_dicts(rows)
    if user.get("role") in {"admin", "ops", "auditor"}:
        items = [attach_knowledge_sensitive_check(item) for item in items]
    return items


def create_knowledge(data: KnowledgeCreate, user: dict[str, Any]) -> dict[str, Any]:
    validate_knowledge_payload(data)
    payload = data.model_dump()
    if user.get("role") == "ops":
        payload["status"] = "pending_review"
    sensitive_check = scan_knowledge_sensitive(payload["title"], payload["content"], payload["tags"])
    if payload["status"] == "published":
        sensitive_check = ensure_knowledge_publishable(payload["title"], payload["content"], payload["tags"])
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
    rag_service.clear_cache()
    return {
        "id": item_id,
        **payload,
        "created_at": now,
        "review_note": "创建时直接发布" if payload["status"] == "published" else "",
        "reviewed_at": now if payload["status"] == "published" else None,
        "reviewed_by": user["id"] if payload["status"] == "published" else None,
        "sensitive_check": sensitive_summary(sensitive_check),
        "updated_at": now,
        "version": 1,
    }


def update_knowledge(item_id: int, data: KnowledgeCreate, user: dict[str, Any]) -> dict[str, Any]:
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
        sensitive_check = scan_knowledge_sensitive(payload["title"], payload["content"], payload["tags"])
        if payload["status"] == "published":
            sensitive_check = ensure_knowledge_publishable(payload["title"], payload["content"], payload["tags"])
        next_version = int(row["version"] or 1) + 1
        reviewed_by = user["id"] if payload["status"] == "published" else None
        reviewed_at = now if payload["status"] == "published" else None
        review_note = "编辑后直接发布" if payload["status"] == "published" else ""
        conn.execute(
            """
            update knowledge
            set title=?,content=?,tags=?,source_type=?,status=?,version=?,reviewed_by=?,reviewed_at=?,review_note=?,updated_at=?
            where id=?
            """,
            (
                payload["title"],
                payload["content"],
                payload["tags"],
                payload["source_type"],
                payload["status"],
                next_version,
                reviewed_by,
                reviewed_at,
                review_note,
                now,
                item_id,
            ),
        )
    audit("knowledge_update", "knowledge", f"{user.get('real_name','')}更新知识：{payload['title']}，状态：{payload['status']}", item_id)
    rag_service.clear_cache()
    return {
        "id": item_id,
        **payload,
        "review_note": review_note,
        "reviewed_at": reviewed_at,
        "reviewed_by": reviewed_by,
        "sensitive_check": sensitive_summary(sensitive_check),
        "updated_at": now,
        "version": next_version,
    }


def change_knowledge_status(item_id: int, data: KnowledgeStatusUpdate, user: dict[str, Any]) -> dict[str, Any]:
    validate_knowledge_payload(data)
    now = utc_now()
    with connect() as conn:
        row = conn.execute("select title,content,tags,status from knowledge where id=?", (item_id,)).fetchone()
        ensure_row_exists(row, "知识条目")
        if row["status"] == data.status:
            return {"id": item_id, "status": data.status, "updated_at": now}
        if data.status == "published":
            ensure_knowledge_publishable(row["title"], row["content"], row["tags"])
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
    rag_service.clear_cache()
    return {"id": item_id, "review_note": review_note, "reviewed_at": now, "reviewed_by": user["id"], "status": data.status, "updated_at": now}
