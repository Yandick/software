"""baseline sqlite schema

Revision ID: 20260522_0001
Revises:
Create Date: 2026-05-22 17:45:00
"""
from __future__ import annotations

from alembic import op


revision = "20260522_0001"
down_revision = None
branch_labels = None
depends_on = None


def _has_column(table_name: str, column_name: str) -> bool:
    rows = op.get_bind().exec_driver_sql(f"pragma table_info({table_name})").fetchall()
    return any(row[1] == column_name for row in rows)


def _add_column_if_missing(table_name: str, column_sql: str) -> None:
    column_name = column_sql.split()[0]
    if not _has_column(table_name, column_name):
        op.execute(f"alter table {table_name} add column {column_sql}")


def upgrade() -> None:
    op.execute("""
    create table if not exists users (
      id integer primary key autoincrement,
      username text unique not null,
      password_hash text not null,
      real_name text not null,
      role text not null,
      department text not null default '运维中心',
      status text not null default 'active',
      created_at text not null
    )
    """)
    op.execute("""
    create table if not exists knowledge (
      id integer primary key autoincrement,
      title text not null,
      content text not null,
      tags text not null default '',
      source_type text not null default 'faq',
      status text not null default 'published',
      version integer not null default 1,
      reviewed_by integer,
      reviewed_at text,
      review_note text not null default '',
      created_at text not null,
      updated_at text not null
    )
    """)
    op.execute("""
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
      created_by integer,
      requester_name text not null default '',
      category text not null default 'general',
      impact_scope text not null default '',
      handled_by integer,
      visited_by integer,
      user_satisfaction_score integer,
      user_feedback text not null default '',
      attachment_url text not null default '',
      log_excerpt text not null default '',
      accepted_at text not null default '',
      handled_at text not null default '',
      closed_at text not null default '',
      created_at text not null,
      updated_at text not null
    )
    """)
    op.execute("""
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
    )
    """)
    op.execute("""
    create table if not exists qa_logs (
      id integer primary key autoincrement,
      question text not null,
      answer text not null,
      need_human integer not null,
      model_status text not null,
      references_json text not null default '[]',
      created_at text not null
    )
    """)
    op.execute("""
    create table if not exists qa_conversations (
      id integer primary key autoincrement,
      user_id integer,
      title text not null default '',
      status text not null default 'active',
      deleted_at text not null default '',
      created_at text not null,
      updated_at text not null
    )
    """)
    op.execute("""
    create table if not exists qa_messages (
      id integer primary key autoincrement,
      conversation_id integer not null,
      role text not null,
      content text not null,
      metadata_json text not null default '{}',
      created_at text not null
    )
    """)
    op.execute("""
    create table if not exists audit_logs (
      id integer primary key autoincrement,
      event_type text not null,
      target_type text not null,
      target_id integer,
      content text not null,
      created_at text not null
    )
    """)
    op.execute("""
    create table if not exists issue_attachments (
      id integer primary key autoincrement,
      original_name text not null,
      stored_name text not null,
      content_type text not null default '',
      size integer not null default 0,
      issue_id integer,
      uploaded_by integer,
      created_at text not null
    )
    """)
    op.execute("""
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
    )
    """)
    op.execute("""
    create table if not exists issue_events (
      id integer primary key autoincrement,
      issue_id integer not null,
      event_type text not null,
      operator_id integer,
      operator_name text not null default '',
      content text not null,
      created_at text not null
    )
    """)

    for column in (
        "created_by integer",
        "requester_name text not null default ''",
        "category text not null default 'general'",
        "impact_scope text not null default ''",
        "handled_by integer",
        "visited_by integer",
        "user_satisfaction_score integer",
        "user_feedback text not null default ''",
        "attachment_url text not null default ''",
        "log_excerpt text not null default ''",
        "accepted_at text not null default ''",
        "handled_at text not null default ''",
        "closed_at text not null default ''",
    ):
        _add_column_if_missing("issues", column)

    for column in (
        "version integer not null default 1",
        "reviewed_by integer",
        "reviewed_at text",
        "review_note text not null default ''",
    ):
        _add_column_if_missing("knowledge", column)

    for column in (
        "owner_name text not null default ''",
        "department text not null default ''",
        "contact_phone text not null default ''",
        "risk_level text not null default 'medium'",
        "expires_at text not null default ''",
    ):
        _add_column_if_missing("ops_accounts", column)

    _add_column_if_missing("account_approvals", "decision_reason text not null default ''")
    _add_column_if_missing("issue_attachments", "issue_id integer")


def downgrade() -> None:
    raise RuntimeError("Baseline downgrade is intentionally unsupported.")
