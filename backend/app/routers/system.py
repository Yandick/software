from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Depends

from ..config import get_settings
from ..security import current_user
from ..services.agent_service import agent_service
from ..services.llm_service import llm_service
from ..services.system_service import readiness as readiness_report
from ..services.system_service import system_info as system_info_report

router = APIRouter(prefix="/api", tags=["system"])


@router.get("/health")
def health() -> dict[str, Any]:
    settings = get_settings()
    return {"status": "ok", "app": settings.app_name, "version": settings.app_version}


@router.get("/ready")
def ready(include_llm: bool = False, require_llm: bool = False) -> dict[str, Any]:
    return readiness_report(include_llm=include_llm, require_llm=require_llm)


@router.get("/system/info")
def system_info(_: dict[str, Any] = Depends(current_user)) -> dict[str, Any]:
    return system_info_report()


@router.get("/llm/status")
def llm_status(_: dict[str, Any] = Depends(current_user)) -> dict[str, Any]:
    return llm_service.status()


@router.get("/agent/status")
def agent_status(_: dict[str, Any] = Depends(current_user)) -> dict[str, Any]:
    return agent_service.status()
