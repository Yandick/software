from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Depends

from ..deps import require_roles
from ..security import current_user
from ..services.demo_service import (
    create_demo_session as create_demo_session_record,
    get_demo_session as get_demo_session_record,
    reset_demo_session as reset_demo_session_record,
    run_demo_step as run_demo_step_record,
)

router = APIRouter(prefix="/api/demo", tags=["demo"])


@router.post("/session")
def create_demo_session(user: dict[str, Any] = Depends(current_user)) -> dict[str, Any]:
    require_roles(user, {"admin"})
    return create_demo_session_record()


@router.get("/session/{session_id}")
def get_demo_session(session_id: str, user: dict[str, Any] = Depends(current_user)) -> dict[str, Any]:
    require_roles(user, {"admin", "ops"})
    return get_demo_session_record(session_id)


@router.post("/session/{session_id}/step")
def run_demo_step(session_id: str, user: dict[str, Any] = Depends(current_user)) -> dict[str, Any]:
    require_roles(user, {"admin"})
    return run_demo_step_record(session_id, user)


@router.post("/session/{session_id}/reset")
def reset_demo_session(session_id: str, user: dict[str, Any] = Depends(current_user)) -> dict[str, Any]:
    require_roles(user, {"admin"})
    return reset_demo_session_record(session_id)
