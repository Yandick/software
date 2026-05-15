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
          permission_scope text not null,
          status text not null default 'active',
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
        """)
        seed_users(conn)
        seed_knowledge(conn)


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


def audit(event_type: str, target_type: str, content: str, target_id: int | None = None) -> None:
    with connect() as conn:
        conn.execute(
            "insert into audit_logs(event_type,target_type,target_id,content,created_at) values(?,?,?,?,?)",
            (event_type, target_type, target_id, content, utc_now()),
        )
