from __future__ import annotations

import hashlib
import re
import unicodedata
from pathlib import Path
from typing import Any

from fastapi import HTTPException

from ..database import audit, connect, rows_to_dicts, utc_now, write_audit
from ..deps import ensure_row_exists
from ..schemas import KnowledgeCreate, KnowledgeStatusUpdate
from .rag_service import DOMAIN_ALIASES, rag_service

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
EXACT_DUPLICATE_RELATIONS = {"exact_content", "exact_title_content"}
NEAR_DUPLICATE_SCORE_THRESHOLD = 0.78
NEAR_DUPLICATE_CONTAINMENT_THRESHOLD = 0.9
NEAR_DUPLICATE_TITLE_SCORE_THRESHOLD = 0.7
NEAR_DUPLICATE_MIN_TERMS = 8
NEAR_DUPLICATE_APPROX_THRESHOLD = 0.86
NEAR_DUPLICATE_SEMANTIC_THRESHOLD = 0.82
AUTO_SKIP_CONTAINMENT_THRESHOLD = 0.94
AUTO_SKIP_MAX_NOVEL_UNITS = 0
AUTO_MERGE_MIN_SCORE = 0.78
MINHASH_SIZE = 32
SIMILARITY_STOP_TERMS = {
    "以及",
    "一个",
    "可以",
    "如果",
    "应该",
    "怎么",
    "如何",
    "处理",
    "问题",
    "用户",
    "系统",
    "确认",
    "需要",
    "信息",
    "记录",
    "进行",
    "当前",
    "相关",
    "时候",
    "什么",
    "步骤",
    "标准",
}
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


def normalize_duplicate_text(value: str) -> str:
    normalized = unicodedata.normalize("NFKC", str(value or "")).lower()
    normalized = re.sub(r"\s+", " ", normalized).strip()
    return normalized


def compact_duplicate_text(value: str) -> str:
    normalized = normalize_duplicate_text(value)
    return re.sub(r"[\s\W_]+", "", normalized, flags=re.UNICODE)


def duplicate_hash(value: str) -> str:
    return hashlib.sha256(compact_duplicate_text(value).encode("utf-8")).hexdigest()


def duplicate_terms(title: str = "", content: str = "", tags: str = "") -> set[str]:
    normalized = normalize_duplicate_text("\n".join([title or "", content or "", tags or ""]))
    terms: set[str] = set()
    for token in re.findall(r"[a-z0-9_.+-]+", normalized):
        if token not in SIMILARITY_STOP_TERMS:
            terms.add(token)
    chinese = "".join(re.findall(r"[\u4e00-\u9fff]", normalized))
    for size in (2, 3, 4):
        for index in range(0, max(len(chinese) - size + 1, 0)):
            gram = chinese[index : index + size]
            if gram and gram not in SIMILARITY_STOP_TERMS:
                terms.add(gram)
    for tag in str(tags or "").split(","):
        normalized_tag = normalize_duplicate_text(tag)
        if len(normalized_tag) >= 2 and normalized_tag not in SIMILARITY_STOP_TERMS:
            terms.add(normalized_tag)
    for canonical, aliases in DOMAIN_ALIASES.items():
        if any(normalize_duplicate_text(alias) in normalized for alias in aliases):
            canonical_norm = normalize_duplicate_text(canonical)
            terms.add(canonical_norm)
            terms.add(f"domain:{canonical_norm}")
            for alias in aliases:
                alias_norm = normalize_duplicate_text(alias)
                if len(alias_norm) >= 2:
                    terms.add(alias_norm)
    return terms


def stable_hash64(value: str) -> int:
    return int(hashlib.blake2b(value.encode("utf-8"), digest_size=8).hexdigest(), 16)


def simhash_signature(terms: set[str]) -> int:
    weights = [0] * 64
    for term in terms:
        hashed = stable_hash64(term)
        weight = 2 if str(term).startswith("domain:") else 1
        for bit in range(64):
            weights[bit] += weight if hashed & (1 << bit) else -weight
    signature = 0
    for bit, weight in enumerate(weights):
        if weight >= 0:
            signature |= 1 << bit
    return signature


def hamming_similarity(left: int, right: int, bits: int = 64) -> float:
    distance = (left ^ right).bit_count()
    return max(0.0, 1.0 - distance / bits)


def minhash_signature(terms: set[str], size: int = MINHASH_SIZE) -> list[int]:
    if not terms:
        return []
    signature: list[int] = []
    sorted_terms = sorted(terms)
    for seed in range(size):
        signature.append(min(stable_hash64(f"{seed}:{term}") for term in sorted_terms))
    return signature


def minhash_similarity(left: list[int], right: list[int]) -> float:
    if not left or not right:
        return 0.0
    limit = min(len(left), len(right))
    return sum(1 for index in range(limit) if left[index] == right[index]) / limit


def term_jaccard(left: set[str], right: set[str]) -> float:
    if not left or not right:
        return 0.0
    return len(left & right) / len(left | right)


def term_containment(left: set[str], right: set[str]) -> float:
    if not left or not right:
        return 0.0
    return len(left & right) / min(len(left), len(right))


def relation_diff_summary(
    *,
    existing_title: str,
    existing_content: str,
    existing_tags: str,
    incoming_title: str,
    incoming_content: str,
    incoming_tags: str,
    relation: str,
    terms: set[str],
    item_terms: set[str],
) -> dict[str, Any]:
    shared_terms = sorted((terms & item_terms), key=lambda value: (-len(value), value))[:12]
    novel_units = novel_merge_units(existing_content, incoming_content)[:8]
    existing_norm = normalize_duplicate_text(f"{existing_title}\n{existing_content}\n{existing_tags}")
    incoming_norm = normalize_duplicate_text(f"{incoming_title}\n{incoming_content}\n{incoming_tags}")
    conflict_signals = []
    for left, right in [("允许", "禁止"), ("启用", "停用"), ("发布", "下线"), ("生产", "测试")]:
        if left in existing_norm and right in incoming_norm:
            conflict_signals.append(f"existing_has_{left}_incoming_has_{right}")
        if right in existing_norm and left in incoming_norm:
            conflict_signals.append(f"existing_has_{right}_incoming_has_{left}")
    scope_terms = {"生产", "测试", "全公司", "部门", "单人", "管理员", "普通用户", "vpn", "mfa", "数据库"}
    scope_overlap = sorted(term for term in (terms & item_terms) if term in scope_terms)
    if relation in EXACT_DUPLICATE_RELATIONS:
        semantic_relation = "exact_duplicate"
        recommended_action = "skip"
    elif conflict_signals:
        semantic_relation = "possible_conflict"
        recommended_action = "human_review"
    elif novel_units:
        semantic_relation = "same_problem_new_solution"
        recommended_action = "merge_candidate"
    elif scope_overlap:
        semantic_relation = "near_duplicate"
        recommended_action = "review_or_skip"
    else:
        semantic_relation = "near_duplicate" if relation == "near_duplicate" else "unique"
        recommended_action = "review" if relation == "near_duplicate" else "insert"
    return {
        "conflict_signals": conflict_signals[:6],
        "novel_units": novel_units,
        "recommended_action": recommended_action,
        "scope_overlap": scope_overlap[:6],
        "semantic_relation": semantic_relation,
        "shared_terms": shared_terms,
    }


def duplicate_policy() -> dict[str, str]:
    return {
        "exact_duplicate": "不新增、不发布重复副本；应复用已有知识或编辑已有条目生成新版本。",
        "near_duplicate": "保留为待审核候选，由管理员比较适用范围、风险提示和版本差异后决定合并、更新旧条目或保留独立条目。",
        "unique": "未发现明显重复，可继续敏感信息审核和发布流程。",
    }


def duplicate_summary(check: dict[str, Any]) -> dict[str, Any]:
    return {
        "blocking": check["blocking"],
        "candidates": check["candidates"],
        "decision": check["decision"],
        "message": check["message"],
    }


def duplicate_candidate_rows(conn: Any) -> list[dict[str, Any]]:
    rows = conn.execute(
        """
        select id,title,content,tags,source_type,status,version,updated_at,review_note
        from knowledge
        where status in ('pending_review','published')
        order by status='published' desc, updated_at desc, id desc
        """
    ).fetchall()
    return rows_to_dicts(rows)


def build_duplicate_check(
    *,
    title: str,
    content: str,
    tags: str = "",
    exclude_id: int | None = None,
    candidates: list[dict[str, Any]] | None = None,
    conn: Any | None = None,
    limit: int = 5,
) -> dict[str, Any]:
    if candidates is None:
        if conn is not None:
            candidates = duplicate_candidate_rows(conn)
        else:
            with connect() as check_conn:
                candidates = duplicate_candidate_rows(check_conn)

    title_content_hash = duplicate_hash(f"{title}\n{content}")
    content_hash = duplicate_hash(content)
    terms = duplicate_terms(title, content, tags)
    title_terms = duplicate_terms(title)
    incoming_minhash = minhash_signature(terms)
    incoming_simhash = simhash_signature(terms)
    scored: list[dict[str, Any]] = []

    for item in candidates:
        item_id = int(item.get("id") or 0)
        if exclude_id is not None and item_id == int(exclude_id):
            continue
        item_title = str(item.get("title", ""))
        item_content = str(item.get("content", ""))
        item_tags = str(item.get("tags", ""))
        existing_title_content_hash = duplicate_hash(f"{item_title}\n{item_content}")
        existing_content_hash = duplicate_hash(item_content)
        item_terms = duplicate_terms(item_title, item_content, item_tags)
        item_title_terms = duplicate_terms(item_title)
        item_minhash = minhash_signature(item_terms)
        item_simhash = simhash_signature(item_terms)

        content_similarity = term_jaccard(terms, item_terms)
        containment = term_containment(terms, item_terms)
        minhash_score = minhash_similarity(incoming_minhash, item_minhash)
        simhash_score = hamming_similarity(incoming_simhash, item_simhash) if terms and item_terms else 0.0
        approx_similarity = max(minhash_score, simhash_score)
        domain_terms = {term for term in terms if str(term).startswith("domain:")}
        item_domain_terms = {term for term in item_terms if str(term).startswith("domain:")}
        domain_similarity = term_containment(domain_terms, item_domain_terms)
        semantic_similarity = term_containment(
            {term for term in terms if str(term).startswith("domain:") or len(str(term)) >= 3},
            {term for term in item_terms if str(term).startswith("domain:") or len(str(term)) >= 3},
        )
        title_similarity = term_jaccard(title_terms, item_title_terms)
        overlap_terms = sorted((terms & item_terms), key=lambda value: (-len(value), value))[:8]
        relation = ""
        score = max(content_similarity, containment * 0.92, approx_similarity * 0.9, semantic_similarity * 0.88, domain_similarity * 0.86)

        if title_content_hash == existing_title_content_hash:
            relation = "exact_title_content"
            score = 1.0
        elif content_hash == existing_content_hash:
            relation = "exact_content"
            score = 1.0
        elif len(terms) >= NEAR_DUPLICATE_MIN_TERMS and len(item_terms) >= NEAR_DUPLICATE_MIN_TERMS and (
            content_similarity >= NEAR_DUPLICATE_SCORE_THRESHOLD
            or containment >= NEAR_DUPLICATE_CONTAINMENT_THRESHOLD
            or approx_similarity >= NEAR_DUPLICATE_APPROX_THRESHOLD
            or semantic_similarity >= NEAR_DUPLICATE_SEMANTIC_THRESHOLD
            or (domain_similarity >= 0.67 and (content_similarity >= 0.12 or len(overlap_terms) >= 6))
            or (
                title_similarity >= NEAR_DUPLICATE_TITLE_SCORE_THRESHOLD
                and content_similarity >= 0.6
            )
        ):
            relation = "near_duplicate"

        if not relation:
            continue
        scored.append(
            {
                "approx_similarity": round(approx_similarity, 4),
                "content_similarity": round(content_similarity, 4),
                "containment": round(containment, 4),
                "domain_similarity": round(domain_similarity, 4),
                "diff": relation_diff_summary(
                    existing_title=item_title,
                    existing_content=item_content,
                    existing_tags=item_tags,
                    incoming_title=title,
                    incoming_content=content,
                    incoming_tags=tags,
                    relation=relation,
                    terms=terms,
                    item_terms=item_terms,
                ),
                "id": item_id,
                "minhash_similarity": round(minhash_score, 4),
                "overlap_terms": overlap_terms,
                "relation": relation,
                "score": round(score, 4),
                "semantic_similarity": round(semantic_similarity, 4),
                "simhash_similarity": round(simhash_score, 4),
                "source_type": item.get("source_type", ""),
                "status": item.get("status", ""),
                "title": item_title,
                "title_similarity": round(title_similarity, 4),
                "updated_at": item.get("updated_at", ""),
                "version": item.get("version", 1),
            }
        )

    scored.sort(
        key=lambda item: (
            item["relation"] in EXACT_DUPLICATE_RELATIONS,
            item["score"],
            item.get("status") == "published",
            item.get("updated_at", ""),
            item.get("id", 0),
        ),
        reverse=True,
    )
    candidates_out = scored[:limit]
    has_exact = any(item["relation"] in EXACT_DUPLICATE_RELATIONS for item in candidates_out)
    if has_exact:
        decision = "exact_duplicate"
        message = "发现精确重复知识，应复用或更新已有条目，不能新增或发布重复副本。"
    elif candidates_out:
        decision = "near_duplicate"
        message = "发现疑似近重复知识，建议审核时比较差异并优先合并到已有条目。"
    else:
        decision = "unique"
        message = "未发现明显重复知识。"
    return {
        "blocking": has_exact,
        "candidates": candidates_out,
        "decision": decision,
        "message": message,
        "policy": duplicate_policy(),
    }


def scan_knowledge_duplicates(
    title: str = "",
    content: str = "",
    tags: str = "",
    exclude_id: int | None = None,
) -> dict[str, Any]:
    return build_duplicate_check(title=title, content=content, tags=tags, exclude_id=exclude_id)


def ensure_not_exact_duplicate(check: dict[str, Any]) -> None:
    if not check.get("blocking"):
        return
    raise HTTPException(
        status_code=409,
        detail={
            "message": check["message"],
            "duplicate_check": check,
        },
    )


def duplicate_review_note(check: dict[str, Any]) -> str:
    if check.get("decision") != "near_duplicate" or not check.get("candidates"):
        return ""
    candidate = check["candidates"][0]
    return f"疑似重复：#{candidate['id']} {candidate['title']}，建议审核时确认是否合并"


def merge_tag_text(*values: str) -> str:
    merged: list[str] = []
    for value in values:
        for tag in str(value or "").split(","):
            cleaned = tag.strip()
            if cleaned and cleaned not in merged:
                merged.append(cleaned)
    return ",".join(merged)


def split_merge_units(content: str) -> list[str]:
    units = [item.strip() for item in re.split(r"(?<=[。！？；;.!?])\s*|\n+", str(content or "")) if item.strip()]
    if units:
        return units
    cleaned = str(content or "").strip()
    return [cleaned] if cleaned else []


def novel_merge_units(existing_content: str, incoming_content: str) -> list[str]:
    existing_compact = compact_duplicate_text(existing_content)
    existing_terms = duplicate_terms(content=existing_content)
    novel: list[str] = []
    seen_compact: set[str] = set()
    for unit in split_merge_units(incoming_content):
        compact = compact_duplicate_text(unit)
        if len(compact) < 8 or compact in seen_compact:
            continue
        seen_compact.add(compact)
        if compact in existing_compact:
            continue
        unit_terms = duplicate_terms(content=unit)
        if unit_terms and existing_terms and term_containment(unit_terms, existing_terms) >= 0.86:
            continue
        novel.append(unit)
    return novel


def build_merged_content(existing_content: str, incoming_content: str, now: str) -> tuple[str, list[str]]:
    novel = novel_merge_units(existing_content, incoming_content)
    if not novel:
        return existing_content, []
    bullets = "\n".join(f"- {unit}" for unit in novel)
    block_time = now.replace("T", " ").removesuffix("Z")
    merged = f"{str(existing_content or '').rstrip()}\n\n补充信息（自动合并 {block_time} UTC）：\n{bullets}"
    return merged, novel


def autonomous_policy() -> dict[str, str]:
    return {
        "inserted": "未发现重复，按角色权限写入目标状态。",
        "skipped_exact_duplicate": "精确重复，不写入新知识。",
        "skipped_redundant_duplicate": "近重复且没有新增信息，不写入新知识。",
        "merged_existing": "近重复但包含新增信息，自动合并到已有知识。",
        "inserted_merge_candidate": "近重复且不能直接改动已发布知识，自动生成待审核合并候选。",
    }


def summarize_knowledge_row(row: Any) -> dict[str, Any]:
    return {
        "id": int(row["id"]),
        "source_type": row["source_type"],
        "status": row["status"],
        "title": row["title"],
        "updated_at": row["updated_at"],
        "version": row["version"],
    }


def build_autonomous_response(
    *,
    action: str,
    duplicate_check: dict[str, Any],
    item: dict[str, Any] | None,
    message: str,
    novel_units: list[str] | None = None,
    redacted: bool = False,
    sensitive_check: dict[str, Any] | None = None,
) -> dict[str, Any]:
    return {
        "action": action,
        "duplicate_check": duplicate_summary(duplicate_check),
        "item": item,
        "message": message,
        "novel_units": novel_units or [],
        "policy": autonomous_policy(),
        "redacted": redacted,
        "sensitive_check": sensitive_summary(sensitive_check or scan_knowledge_sensitive()),
    }


def autonomous_ingest_payload(
    *,
    conn: Any,
    payload: dict[str, Any],
    user: dict[str, Any],
    now: str,
    audit_prefix: str = "自动知识入库",
    candidates: list[dict[str, Any]] | None = None,
) -> dict[str, Any]:
    if user.get("role") == "ops":
        payload["status"] = "pending_review"

    original_check = scan_knowledge_sensitive(payload["title"], payload["content"], payload["tags"])
    redacted = original_check["redacted"]
    payload = {
        **payload,
        "content": redacted["content"],
        "tags": redacted["tags"],
        "title": redacted["title"],
    }
    redacted_changed = (
        payload["title"] != redacted["title"]
        or payload["content"] != redacted["content"]
        or payload["tags"] != redacted["tags"]
        or original_check["has_sensitive"]
    )
    publishable_check = ensure_knowledge_publishable(payload["title"], payload["content"], payload["tags"]) if payload["status"] == "published" else scan_knowledge_sensitive(payload["title"], payload["content"], payload["tags"])
    duplicate_check = build_duplicate_check(
        title=payload["title"],
        content=payload["content"],
        tags=payload["tags"],
        candidates=candidates,
        conn=None if candidates is not None else conn,
    )

    if duplicate_check["decision"] == "exact_duplicate":
        candidate = duplicate_check["candidates"][0] if duplicate_check["candidates"] else {}
        write_audit(
            conn,
            "knowledge_auto_skip",
            "knowledge",
            f"{audit_prefix}跳过精确重复：{payload['title']}，匹配 #{candidate.get('id')}",
            int(candidate["id"]) if candidate.get("id") else None,
        )
        return build_autonomous_response(
            action="skipped_exact_duplicate",
            duplicate_check=duplicate_check,
            item=candidate or None,
            message="精确重复，已自动跳过，未写入新知识。",
            redacted=redacted_changed,
            sensitive_check=publishable_check,
        )

    if duplicate_check["decision"] == "near_duplicate" and duplicate_check["candidates"]:
        candidate = duplicate_check["candidates"][0]
        row = conn.execute("select * from knowledge where id=?", (int(candidate["id"]),)).fetchone()
        ensure_row_exists(row, "重复候选知识条目")
        merged_content, novel_units = build_merged_content(row["content"], payload["content"], now)
        if candidate.get("containment", 0) >= AUTO_SKIP_CONTAINMENT_THRESHOLD and len(novel_units) <= AUTO_SKIP_MAX_NOVEL_UNITS:
            write_audit(
                conn,
                "knowledge_auto_skip",
                "knowledge",
                f"{audit_prefix}跳过冗余近重复：{payload['title']}，匹配 #{row['id']}",
                int(row["id"]),
            )
            return build_autonomous_response(
                action="skipped_redundant_duplicate",
                duplicate_check=duplicate_check,
                item=summarize_knowledge_row(row),
                message="近重复且没有可合并的新增信息，已自动跳过。",
                redacted=redacted_changed,
                sensitive_check=publishable_check,
            )

        can_update_published = row["status"] == "published" and payload["status"] == "published" and user.get("role") == "admin"
        can_update_existing = row["status"] == "pending_review" or can_update_published
        if can_update_existing and candidate.get("score", 0) >= AUTO_MERGE_MIN_SCORE:
            next_version = int(row["version"] or 1) + 1
            merged_tags = merge_tag_text(row["tags"], payload["tags"])
            review_note = (
                f"自动合并新增信息：{payload['title']}"
                if row["status"] == "published"
                else f"自动合并待审核重复候选：{payload['title']}"
            )
            conn.execute(
                """
                update knowledge
                set content=?,tags=?,version=?,reviewed_by=?,reviewed_at=?,review_note=?,updated_at=?
                where id=?
                """,
                (
                    merged_content,
                    merged_tags,
                    next_version,
                    user["id"] if row["status"] == "published" else None,
                    now if row["status"] == "published" else None,
                    review_note,
                    now,
                    int(row["id"]),
                ),
            )
            updated = conn.execute("select * from knowledge where id=?", (int(row["id"]),)).fetchone()
            write_audit(
                conn,
                "knowledge_auto_merge",
                "knowledge",
                f"{audit_prefix}合并近重复知识：{payload['title']} -> #{row['id']}，新增 {len(novel_units)} 条信息",
                int(row["id"]),
            )
            return build_autonomous_response(
                action="merged_existing",
                duplicate_check=duplicate_check,
                item=summarize_knowledge_row(updated),
                message="近重复但包含新增信息，已自动合并到已有知识。",
                novel_units=novel_units,
                redacted=redacted_changed,
                sensitive_check=publishable_check,
            )

        candidate_title = payload["title"] if payload["title"].startswith("自动合并候选：") else f"自动合并候选：{payload['title']}"
        review_note = f"自动识别为 #{row['id']} 的近重复补充，等待管理员确认发布"
        cur = conn.execute(
            """
            insert into knowledge(
              title,content,tags,source_type,status,version,reviewed_by,reviewed_at,review_note,created_at,updated_at
            ) values(?,?,?,?,?,?,?,?,?,?,?)
            """,
            (
                candidate_title,
                payload["content"],
                payload["tags"],
                payload["source_type"],
                "pending_review",
                1,
                None,
                None,
                review_note,
                now,
                now,
            ),
        )
        inserted = conn.execute("select * from knowledge where id=?", (int(cur.lastrowid),)).fetchone()
        write_audit(
            conn,
            "knowledge_auto_candidate",
            "knowledge",
            f"{audit_prefix}生成近重复合并候选：{candidate_title}，关联 #{row['id']}",
            int(cur.lastrowid),
        )
        return build_autonomous_response(
            action="inserted_merge_candidate",
            duplicate_check=duplicate_check,
            item=summarize_knowledge_row(inserted),
            message="近重复包含新增信息，但当前权限/目标状态不适合直接改动已发布知识，已自动生成待审核合并候选。",
            novel_units=novel_units,
            redacted=redacted_changed,
            sensitive_check=publishable_check,
        )

    review_note = "自动入库直接发布" if payload["status"] == "published" else "自动入库待审核"
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
            review_note,
            now,
            now,
        ),
    )
    inserted = conn.execute("select * from knowledge where id=?", (int(cur.lastrowid),)).fetchone()
    write_audit(
        conn,
        "knowledge_auto_insert",
        "knowledge",
        f"{audit_prefix}新增知识：{payload['title']}，状态：{payload['status']}",
        int(cur.lastrowid),
    )
    return build_autonomous_response(
        action="inserted",
        duplicate_check=duplicate_check,
        item=summarize_knowledge_row(inserted),
        message="未发现重复，已按目标状态自动写入知识库。",
        redacted=redacted_changed,
        sensitive_check=publishable_check,
    )


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
    skipped_chunks: list[dict[str, Any]] = []
    action_counts: dict[str, int] = {}

    with connect() as conn:
        duplicate_candidates = duplicate_candidate_rows(conn)
        for index, chunk in enumerate(chunks, start=1):
            chunk_title = f"{base_title}（片段 {index}/{len(chunks)}）" if len(chunks) > 1 else base_title
            original_check = scan_knowledge_sensitive(chunk_title, chunk, tag_text)
            result = autonomous_ingest_payload(
                conn=conn,
                payload={
                    "content": chunk,
                    "source_type": "document",
                    "status": "pending_review",
                    "tags": tag_text,
                    "title": chunk_title,
                },
                user=user,
                now=now,
                audit_prefix=f"文档 {original_name} 自动导入",
                candidates=duplicate_candidates,
            )
            action = str(result["action"])
            action_counts[action] = action_counts.get(action, 0) + 1
            if action.startswith("skipped"):
                skipped_chunks.append(
                    {
                        "action": action,
                        "duplicate_check": result["duplicate_check"],
                        "reason": action,
                        "title": chunk_title,
                    }
                )
                continue
            item = result["item"] or {}
            imported_chunks.append(
                {
                    "action": action,
                    "duplicate_check": result["duplicate_check"],
                    "id": item["id"],
                    "redacted": bool(original_check["has_sensitive"]),
                    "sensitive_check": sensitive_summary(original_check),
                    "source_type": item.get("source_type", "document"),
                    "status": item.get("status", "pending_review"),
                    "title": item.get("title", chunk_title),
                }
            )
            duplicate_candidates = duplicate_candidate_rows(conn)

    redacted_count = sum(1 for chunk in imported_chunks if chunk["redacted"])
    audit(
        "knowledge_document_import",
        "knowledge",
        f"{user.get('real_name','')}导入文档知识：{original_name}，处理 {len(imported_chunks)} 个片段，脱敏 {redacted_count} 个片段，跳过重复 {len(skipped_chunks)} 个片段",
    )
    rag_service.clear_cache()
    return {
        "action_counts": action_counts,
        "chunk_count": len(imported_chunks),
        "chunks": imported_chunks,
        "filename": original_name,
        "redacted_count": redacted_count,
        "skipped_chunks": skipped_chunks,
        "skipped_count": len(skipped_chunks),
        "status": "pending_review",
    }


def autonomous_ingest_knowledge(data: KnowledgeCreate, user: dict[str, Any]) -> dict[str, Any]:
    validate_knowledge_payload(data)
    payload = data.model_dump()
    now = utc_now()
    with connect() as conn:
        result = autonomous_ingest_payload(
            conn=conn,
            payload=payload,
            user=user,
            now=now,
            audit_prefix=f"{user.get('real_name','')}自动处理知识",
        )
    rag_service.clear_cache()
    return result


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
        duplicate_candidates: list[dict[str, Any]] | None = None
        for item in items:
            if item.get("status") != "pending_review":
                continue
            if duplicate_candidates is None:
                with connect() as conn:
                    duplicate_candidates = duplicate_candidate_rows(conn)
            attach_knowledge_sensitive_check(item)
            item["duplicate_check"] = duplicate_summary(
                build_duplicate_check(
                    title=item.get("title", ""),
                    content=item.get("content", ""),
                    tags=item.get("tags", ""),
                    exclude_id=int(item.get("id") or 0),
                    candidates=duplicate_candidates,
                )
            )
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
        duplicate_check = build_duplicate_check(
            title=payload["title"],
            content=payload["content"],
            tags=payload["tags"],
            conn=conn,
        )
        ensure_not_exact_duplicate(duplicate_check)
        review_note = "创建时直接发布" if payload["status"] == "published" else duplicate_review_note(duplicate_check)
        if payload["status"] == "published" and duplicate_review_note(duplicate_check):
            review_note = f"{review_note}；{duplicate_review_note(duplicate_check)}"
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
                review_note,
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
        "duplicate_check": duplicate_summary(duplicate_check),
        "review_note": review_note,
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
        duplicate_check = build_duplicate_check(
            title=payload["title"],
            content=payload["content"],
            tags=payload["tags"],
            exclude_id=item_id,
            conn=conn,
        )
        ensure_not_exact_duplicate(duplicate_check)
        next_version = int(row["version"] or 1) + 1
        reviewed_by = user["id"] if payload["status"] == "published" else None
        reviewed_at = now if payload["status"] == "published" else None
        review_note = "编辑后直接发布" if payload["status"] == "published" else ""
        near_note = duplicate_review_note(duplicate_check)
        if near_note:
            review_note = f"{review_note}；{near_note}" if review_note else near_note
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
        "duplicate_check": duplicate_summary(duplicate_check),
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
        duplicate_check = build_duplicate_check(
            title=row["title"],
            content=row["content"],
            tags=row["tags"],
            exclude_id=item_id,
            conn=conn,
        )
        if data.status == "published":
            ensure_knowledge_publishable(row["title"], row["content"], row["tags"])
            ensure_not_exact_duplicate(duplicate_check)
        review_note = data.review_note.strip() or (
            "审核通过并发布" if data.status == "published" else
            "退回待审核" if data.status == "pending_review" else
            "管理员下线"
        )
        near_note = duplicate_review_note(duplicate_check)
        if data.status == "published" and near_note:
            review_note = f"{review_note}；{near_note}"
        conn.execute(
            "update knowledge set status=?,reviewed_by=?,reviewed_at=?,review_note=?,updated_at=? where id=?",
            (data.status, user["id"], now, review_note, now, item_id),
        )
    audit("knowledge_status", "knowledge", f"知识状态变更：{row['title']} {row['status']} -> {data.status}，审核意见：{review_note}", item_id)
    rag_service.clear_cache()
    return {
        "duplicate_check": duplicate_summary(duplicate_check),
        "id": item_id,
        "review_note": review_note,
        "reviewed_at": now,
        "reviewed_by": user["id"],
        "status": data.status,
        "updated_at": now,
    }
