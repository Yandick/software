from __future__ import annotations

import hashlib
import json
import sqlite3
from contextlib import contextmanager
from datetime import datetime
from pathlib import Path
from typing import Any, Iterator

from .config import get_settings


def utc_now() -> str:
    return datetime.utcnow().isoformat(timespec="seconds") + "Z"


def _hash_password(password: str) -> str:
    return hashlib.sha256(password.encode("utf-8")).hexdigest()


@contextmanager
def connect() -> Iterator[sqlite3.Connection]:
    settings = get_settings()
    settings.db_path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(settings.db_path)
    conn.row_factory = sqlite3.Row
    try:
        yield conn
        conn.commit()
    finally:
        conn.close()


def rows_to_dicts(rows: list[sqlite3.Row]) -> list[dict[str, Any]]:
    return [dict(row) for row in rows]


def init_db() -> None:
    with connect() as conn:
        conn.executescript("""
        create table if not exists users (
          id integer primary key autoincrement,
          username text unique not null,
          password_hash text not null,
          real_name text not null,
          role text not null,
          department text not null default '运维中心',
          status text not null default 'active',
          created_at text not null
        );
        create table if not exists knowledge (
          id integer primary key autoincrement,
          title text not null,
          content text not null,
          tags text not null default '',
          source_type text not null default 'faq',
          status text not null default 'published',
          created_at text not null,
          updated_at text not null
        );
        create table if not exists issues (
          id integer primary key autoincrement,
          title text not null,
          description text not null,
          contact_phone text not null default '',
          priority text not null default 'medium',
          status text not null default 'pending',
          solution text not null default '',
          resolved integer not null default 0,
          satisfaction_score integer,
          visit_result text not null default '',
          created_at text not null,
          updated_at text not null
        );
        create table if not exists ops_accounts (
          id integer primary key autoincrement,
          account_name text unique not null,
          owner_name text not null default '',
          department text not null default '',
          contact_phone text not null default '',
          permission_scope text not null,
          status text not null default 'active',
          risk_level text not null default 'medium',
          expires_at text not null default '',
          remark text not null default '',
          created_at text not null,
          updated_at text not null
        );
        create table if not exists qa_logs (
          id integer primary key autoincrement,
          question text not null,
          answer text not null,
          need_human integer not null,
          model_status text not null,
          references_json text not null default '[]',
          created_at text not null
        );
        create table if not exists audit_logs (
          id integer primary key autoincrement,
          event_type text not null,
          target_type text not null,
          target_id integer,
          content text not null,
          created_at text not null
        );
        create table if not exists issue_attachments (
          id integer primary key autoincrement,
          original_name text not null,
          stored_name text not null,
          content_type text not null default '',
          size integer not null default 0,
          uploaded_by integer,
          created_at text not null
        );
        create table if not exists account_approvals (
          id integer primary key autoincrement,
          account_id integer not null,
          action text not null,
          payload_json text not null default '{}',
          reason text not null default '',
          status text not null default 'pending',
          requested_by integer,
          approved_by integer,
          decision_reason text not null default '',
          decided_at text,
          created_at text not null,
          updated_at text not null
        );
        """)
        ensure_issue_columns(conn)
        ensure_account_columns(conn)
        ensure_account_approval_columns(conn)
        ensure_issue_events(conn)
        seed_users(conn)
        seed_knowledge(conn)


def ensure_issue_columns(conn: sqlite3.Connection) -> None:
    existing = {row["name"] for row in conn.execute("pragma table_info(issues)").fetchall()}
    columns = {
        "created_by": "integer",
        "requester_name": "text not null default ''",
        "category": "text not null default 'general'",
        "impact_scope": "text not null default ''",
        "handled_by": "integer",
        "visited_by": "integer",
        "user_satisfaction_score": "integer",
        "user_feedback": "text not null default ''",
        "attachment_url": "text not null default ''",
        "log_excerpt": "text not null default ''",
    }
    for name, definition in columns.items():
        if name not in existing:
            conn.execute(f"alter table issues add column {name} {definition}")


def ensure_account_columns(conn: sqlite3.Connection) -> None:
    existing = {row["name"] for row in conn.execute("pragma table_info(ops_accounts)").fetchall()}
    columns = {
        "owner_name": "text not null default ''",
        "department": "text not null default ''",
        "contact_phone": "text not null default ''",
        "risk_level": "text not null default 'medium'",
        "expires_at": "text not null default ''",
    }
    for name, definition in columns.items():
        if name not in existing:
            conn.execute(f"alter table ops_accounts add column {name} {definition}")


def ensure_issue_events(conn: sqlite3.Connection) -> None:
    conn.execute("""
    create table if not exists issue_events (
      id integer primary key autoincrement,
      issue_id integer not null,
      event_type text not null,
      operator_id integer,
      operator_name text not null default '',
      content text not null,
      created_at text not null
    );
    """)


def ensure_account_approval_columns(conn: sqlite3.Connection) -> None:
    existing = {row["name"] for row in conn.execute("pragma table_info(account_approvals)").fetchall()}
    columns = {
        "decision_reason": "text not null default ''",
    }
    for name, definition in columns.items():
        if name not in existing:
            conn.execute(f"alter table account_approvals add column {name} {definition}")


def seed_users(conn: sqlite3.Connection) -> None:
    users = [
        ("admin", "admin123", "系统管理员", "admin", "信息技术部"),
        ("ops", "ops123", "运维人员", "ops", "运维中心"),
        ("user", "user123", "普通用户", "user", "业务部门"),
        ("auditor", "audit123", "审计员", "auditor", "审计部"),
    ]
    for username, password, real_name, role, department in users:
        conn.execute(
            "insert or ignore into users(username,password_hash,real_name,role,department,created_at) values(?,?,?,?,?,?)",
            (username, _hash_password(password), real_name, role, department, utc_now()),
        )


def seed_knowledge(conn: sqlite3.Connection) -> None:
    if conn.execute("select count(*) from knowledge").fetchone()[0]:
        return
    seed_file = Path(__file__).parent / "data" / "knowledge_seed.json"
    items = json.loads(seed_file.read_text(encoding="utf-8"))
    now = utc_now()
    for item in items:
        conn.execute(
            "insert into knowledge(title,content,tags,source_type,status,created_at,updated_at) values(?,?,?,?,?,?,?)",
            (item["title"], item["content"], item.get("tags", ""), item.get("source_type", "faq"), "published", now, now),
        )


def get_user_by_username(username: str) -> dict[str, Any] | None:
    with connect() as conn:
        row = conn.execute("select * from users where username=?", (username,)).fetchone()
        return dict(row) if row else None


def write_audit(
    conn: sqlite3.Connection,
    event_type: str,
    target_type: str,
    content: str,
    target_id: int | None = None,
) -> None:
    conn.execute(
        "insert into audit_logs(event_type,target_type,target_id,content,created_at) values(?,?,?,?,?)",
        (event_type, target_type, target_id, content, utc_now()),
    )


def audit(event_type: str, target_type: str, content: str, target_id: int | None = None) -> None:
    with connect() as conn:
        write_audit(conn, event_type, target_type, content, target_id)


def issue_event(
    conn: sqlite3.Connection,
    issue_id: int,
    event_type: str,
    operator: dict[str, Any],
    content: str,
) -> None:
    conn.execute(
        "insert into issue_events(issue_id,event_type,operator_id,operator_name,content,created_at) values(?,?,?,?,?,?)",
        (issue_id, event_type, operator.get("id"), operator.get("real_name", ""), content, utc_now()),
    )
