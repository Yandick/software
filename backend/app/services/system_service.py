from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from ..config import get_settings, validate_production_settings
from ..database import connect
from .llm_service import llm_service

PROJECT_ROOT = Path(__file__).resolve().parents[3]
REQUIRED_TABLES = {
    "account_approvals",
    "audit_logs",
    "issue_attachments",
    "issue_events",
    "issues",
    "knowledge",
    "ops_accounts",
    "qa_conversations",
    "qa_logs",
    "qa_messages",
    "users",
}
REQUIRED_COLUMNS = {
    "account_approvals": {
        "account_id",
        "action",
        "approved_by",
        "decision_reason",
        "payload_json",
        "requested_by",
        "status",
    },
    "audit_logs": {"content", "created_at", "event_type", "target_id", "target_type"},
    "issue_attachments": {"issue_id", "original_name", "stored_name", "uploaded_by"},
    "issue_events": {"content", "event_type", "issue_id", "operator_id", "operator_name"},
    "issues": {
        "accepted_at",
        "attachment_url",
        "category",
        "closed_at",
        "created_by",
        "handled_at",
        "handled_by",
        "impact_scope",
        "log_excerpt",
        "requester_name",
        "status",
        "user_feedback",
        "user_satisfaction_score",
        "visited_by",
    },
    "knowledge": {"review_note", "reviewed_at", "reviewed_by", "status", "version"},
    "ops_accounts": {"contact_phone", "department", "expires_at", "owner_name", "risk_level", "status"},
    "qa_conversations": {"deleted_at", "status", "title", "updated_at", "user_id"},
    "qa_logs": {"model_status", "need_human", "references_json"},
    "qa_messages": {"conversation_id", "metadata_json", "role"},
    "users": {"department", "password_hash", "role", "status", "username"},
}


def _display_path(path: Path) -> str:
    try:
        return str(path.resolve().relative_to(PROJECT_ROOT.resolve()))
    except ValueError:
        return str(path)


def _resolve_project_path(value: str) -> Path:
    path = Path(value)
    if path.is_absolute():
        return path
    return PROJECT_ROOT / path


def _frontend_package() -> dict[str, str]:
    package_file = PROJECT_ROOT / "frontend" / "package.json"
    if not package_file.exists():
        return {"name": "", "version": "", "package_file": "frontend/package.json"}
    data = json.loads(package_file.read_text(encoding="utf-8"))
    return {
        "name": str(data.get("name", "")),
        "version": str(data.get("version", "")),
        "package_file": "frontend/package.json",
    }


def _check_config() -> dict[str, Any]:
    try:
        validate_production_settings(get_settings())
    except RuntimeError as exc:
        return {"name": "config", "ok": False, "critical": True, "detail": str(exc)}
    return {"name": "config", "ok": True, "critical": True, "detail": "settings accepted"}


def _check_database() -> dict[str, Any]:
    try:
        with connect() as conn:
            conn.execute("select 1").fetchone()
            rows = conn.execute("select name from sqlite_master where type='table'").fetchall()
            table_names = {row["name"] for row in rows}
            missing = sorted(REQUIRED_TABLES - table_names)
            missing_columns = {}
            for table_name, required_columns in REQUIRED_COLUMNS.items():
                if table_name in missing:
                    continue
                columns = {row["name"] for row in conn.execute(f"pragma table_info({table_name})").fetchall()}
                table_missing_columns = sorted(required_columns - columns)
                if table_missing_columns:
                    missing_columns[table_name] = table_missing_columns
    except Exception as exc:
        return {"name": "database", "ok": False, "critical": True, "detail": exc.__class__.__name__}
    if missing or missing_columns:
        detail: dict[str, Any] = {}
        if missing:
            detail["missing_tables"] = missing
        if missing_columns:
            detail["missing_columns"] = missing_columns
        return {"name": "database", "ok": False, "critical": True, "detail": detail}
    return {
        "name": "database",
        "ok": True,
        "critical": True,
        "detail": {"schema": "available", "tables": len(REQUIRED_TABLES)},
    }


def _check_llm(require_llm: bool) -> dict[str, Any]:
    status = llm_service.status()
    return {
        "name": "vllm",
        "ok": status.get("ready") is True,
        "critical": require_llm,
        "detail": {
            "mode": status.get("mode"),
            "ready": status.get("ready"),
            "model": status.get("vllm_model_name"),
            "error": status.get("error", ""),
        },
    }


def system_info() -> dict[str, Any]:
    settings = get_settings()
    model_path = _resolve_project_path(settings.model_path)
    return {
        "app": settings.app_name,
        "version": settings.app_version,
        "environment": settings.environment_name,
        "api_prefix": settings.api_prefix,
        "database": {
            "engine": "sqlite",
            "path": _display_path(settings.db_path),
        },
        "frontend": _frontend_package(),
        "model": {
            "path": _display_path(model_path),
            "path_exists": model_path.exists(),
            "vllm_base_url": settings.vllm_base_url,
            "vllm_model_name": settings.vllm_model_name,
            "enable_thinking_default": settings.enable_thinking,
        },
        "features": {
            "local_llm_required": True,
            "rag": True,
            "portal": True,
            "rbac": True,
            "audit": True,
            "demo_accounts_seeded": settings.seed_demo_accounts,
        },
    }


def readiness(include_llm: bool = False, require_llm: bool = False) -> dict[str, Any]:
    checks = [_check_config(), _check_database()]
    if include_llm:
        checks.append(_check_llm(require_llm=require_llm))
    failed_critical = [item for item in checks if item["critical"] and not item["ok"]]
    failed_warning = [item for item in checks if not item["critical"] and not item["ok"]]
    if failed_critical:
        status = "error"
    elif failed_warning:
        status = "degraded"
    else:
        status = "ok"
    return {
        "status": status,
        "app": get_settings().app_name,
        "version": get_settings().app_version,
        "checks": checks,
    }
