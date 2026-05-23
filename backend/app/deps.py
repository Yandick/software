from __future__ import annotations

from typing import Any

from fastapi import HTTPException


def require_roles(user: dict[str, Any], roles: set[str]) -> None:
    if user.get("role") not in roles:
        raise HTTPException(status_code=403, detail="Permission denied")


def public_user(user: dict[str, Any]) -> dict[str, Any]:
    return {key: user[key] for key in ["id", "username", "real_name", "role", "department", "status"] if key in user}


def ensure_row_exists(row: Any, target: str = "记录") -> None:
    if not row:
        raise HTTPException(status_code=404, detail=f"{target}不存在")
