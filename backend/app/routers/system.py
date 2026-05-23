from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Depends

from ..config import get_settings
from ..security import current_user
from ..services.agent_service import agent_service
from ..services.llm_service import llm_service

router = APIRouter(prefix="/api", tags=["system"])


@router.get("/health")
def health() -> dict[str, Any]:
    return {"status": "ok", "app": get_settings().app_name}


@router.get("/llm/status")
def llm_status(_: dict[str, Any] = Depends(current_user)) -> dict[str, Any]:
    return llm_service.status()


@router.get("/agent/status")
def agent_status(_: dict[str, Any] = Depends(current_user)) -> dict[str, Any]:
    return agent_service.status()
