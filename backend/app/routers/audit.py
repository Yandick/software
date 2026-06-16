from __future__ import annotations

from datetime import datetime
from typing import Any

from fastapi import APIRouter, Depends, Query

from ..database import audit, connect
from ..deps import require_roles
from ..security import current_user
from ..services.audit_service import build_audit_csv, build_stats, fetch_audit_payload

router = APIRouter(prefix="/api/audit", tags=["audit"])


@router.get("/logs")
def audit_logs(
    limit: int = Query(100, ge=1, le=500),
    event_type: str = "",
    target_type: str = "",
    q: str = "",
    need_human: str = "",
    user: dict[str, Any] = Depends(current_user),
) -> dict[str, Any]:
    require_roles(user, {"admin", "auditor"})
    with connect() as conn:
        return fetch_audit_payload(conn, limit, event_type, target_type, q, need_human)


@router.get("/export")
def export_audit_logs(
    limit: int = Query(500, ge=1, le=2000),
    event_type: str = "",
    target_type: str = "",
    q: str = "",
    need_human: str = "",
    user: dict[str, Any] = Depends(current_user),
) -> dict[str, Any]:
    require_roles(user, {"admin", "auditor"})
    with connect() as conn:
        payload = fetch_audit_payload(conn, limit, event_type, target_type, q, need_human)
    audit("audit_export", "audit_log", f"导出审计 CSV：操作日志 {len(payload['audit'])} 条，问答日志 {len(payload['qa'])} 条")
    return {
        "content": build_audit_csv(payload["audit"], payload["qa"]),
        "count": len(payload["audit"]) + len(payload["qa"]),
        "filename": f"audit_logs_{datetime.utcnow().strftime('%Y%m%d%H%M%S')}.csv",
    }


@router.get("/stats")
def stats(user: dict[str, Any] = Depends(current_user)) -> dict[str, Any]:
    if user.get("role") != "user":
        require_roles(user, {"admin", "ops", "auditor"})
    return build_stats(user)
