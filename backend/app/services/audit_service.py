from __future__ import annotations

import csv
import io
import json
from typing import Any

from ..database import connect, rows_to_dicts

ISSUE_ACTIVE_STATUSES = {"accepted", "handled", "need_user_info", "pending", "processing", "submitted"}
ISSUE_PENDING_VISIT_STATUSES = {"handled", "pending_visit"}
CSV_DANGEROUS_PREFIXES = ("=", "+", "-", "@")


def safe_csv_value(value: Any) -> Any:
    if not isinstance(value, str):
        return value
    visible_value = value.lstrip(" \t\r\n")
    if visible_value and visible_value[0] in CSV_DANGEROUS_PREFIXES:
        return f"'{value}"
    return value


def fetch_audit_payload(
    conn: Any,
    limit: int,
    event_type: str = "",
    target_type: str = "",
    q: str = "",
    need_human: str = "",
) -> dict[str, Any]:
    limit = max(1, min(int(limit), 2000))
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
        "event_summary": rows_to_dicts(event_rows),
        "qa": rows_to_dicts(qa_rows),
        "target_summary": rows_to_dicts(target_rows),
    }


def build_audit_csv(audit_rows: list[dict[str, Any]], qa_rows: list[dict[str, Any]]) -> str:
    output = io.StringIO()
    writer = csv.writer(output)
    writer.writerow(
        [
            "log_type",
            "id",
            "event_type",
            "target_type",
            "target_id",
            "content",
            "question",
            "answer",
            "need_human",
            "model_status",
            "created_at",
        ]
    )
    for row in audit_rows:
        writer.writerow(
            [
                "audit",
                row.get("id", ""),
                row.get("event_type", ""),
                row.get("target_type", ""),
                row.get("target_id", ""),
                safe_csv_value(row.get("content", "")),
                "",
                "",
                "",
                "",
                row.get("created_at", ""),
            ]
        )
    for row in qa_rows:
        writer.writerow(
            [
                "qa",
                row.get("id", ""),
                "",
                "qa_log",
                row.get("id", ""),
                "",
                safe_csv_value(row.get("question", "")),
                safe_csv_value(row.get("answer", "")),
                row.get("need_human", ""),
                row.get("model_status", ""),
                row.get("created_at", ""),
            ]
        )
    return output.getvalue()


def build_stats(user: dict[str, Any]) -> dict[str, Any]:
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
                "closed_issues": closed,
                "handled_issues": handled_issues,
                "human_transfer_rate": 0,
                "issues": issues,
                "pending_issues": pending_issues,
                "self_solved_rate": 0,
                "total_qa": total_qa,
            }
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
        "accounts": accounts,
        "active_accounts": active_accounts,
        "audit_count": audit_count,
        "average_rag_confidence": rag_score_sum / rag_hit_count if rag_hit_count else 0,
        "closed_issues": closed,
        "frozen_accounts": frozen_accounts,
        "handled_issues": handled_issues,
        "human_transfer_rate": human / total_qa if total_qa else 0,
        "issues": issues,
        "knowledge": knowledge,
        "knowledge_hit_rate": rag_hit_count / total_qa if total_qa else 0,
        "pending_issues": pending_issues,
        "pending_knowledge": pending_knowledge,
        "published_knowledge": published_knowledge,
        "self_solved_rate": 1 - (human / total_qa) if total_qa else 0,
        "total_qa": total_qa,
    }
