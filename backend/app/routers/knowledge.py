from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Depends, File, Form, UploadFile

from ..deps import require_roles
from ..schemas import KnowledgeCreate, KnowledgeDuplicateCheckRequest, KnowledgeSensitiveCheckRequest, KnowledgeStatusUpdate
from ..security import current_user
from ..services.agent_service import agent_service
from ..services.knowledge_service import (
    change_knowledge_status as change_knowledge_status_record,
    delete_knowledge as delete_knowledge_record,
    create_knowledge as create_knowledge_record,
    import_knowledge_document,
    list_knowledge as list_knowledge_rows,
    scan_knowledge_duplicates,
    scan_knowledge_sensitive,
    update_knowledge as update_knowledge_record,
)

router = APIRouter(prefix="/api", tags=["knowledge"])


@router.get("/knowledge")
def list_knowledge(
    q: str = "",
    status: str = "",
    source_type: str = "",
    user: dict[str, Any] = Depends(current_user),
) -> list[dict[str, Any]]:
    return list_knowledge_rows(q, status, source_type, user)


@router.post("/knowledge/sensitive-check")
def check_knowledge_sensitive(
    data: KnowledgeSensitiveCheckRequest,
    user: dict[str, Any] = Depends(current_user),
) -> dict[str, Any]:
    require_roles(user, {"admin", "ops", "auditor"})
    return scan_knowledge_sensitive(data.title, data.content, data.tags)


@router.post("/knowledge/duplicate-check")
def check_knowledge_duplicates(
    data: KnowledgeDuplicateCheckRequest,
    user: dict[str, Any] = Depends(current_user),
) -> dict[str, Any]:
    require_roles(user, {"admin", "ops", "auditor"})
    return scan_knowledge_duplicates(data.title, data.content, data.tags, data.exclude_id)


@router.post("/knowledge")
def create_knowledge(data: KnowledgeCreate, user: dict[str, Any] = Depends(current_user)) -> dict[str, Any]:
    require_roles(user, {"admin", "ops"})
    return create_knowledge_record(data, user)


@router.post("/knowledge/autonomous-ingest")
def autonomous_ingest_knowledge(data: KnowledgeCreate, user: dict[str, Any] = Depends(current_user)) -> dict[str, Any]:
    require_roles(user, {"admin", "ops"})
    return agent_service.curate_knowledge(data, user)


@router.post("/knowledge/documents/upload")
async def upload_knowledge_document(
    file: UploadFile = File(...),
    title: str = Form(""),
    tags: str = Form(""),
    user: dict[str, Any] = Depends(current_user),
) -> dict[str, Any]:
    require_roles(user, {"admin", "ops"})
    raw = await file.read()
    return import_knowledge_document(
        content_type=file.content_type or "",
        original_name=file.filename or "uploaded.txt",
        raw=raw,
        tags=tags,
        title=title,
        user=user,
    )


@router.put("/knowledge/{item_id}")
def update_knowledge(item_id: int, data: KnowledgeCreate, user: dict[str, Any] = Depends(current_user)) -> dict[str, Any]:
    require_roles(user, {"admin", "ops"})
    return update_knowledge_record(item_id, data, user)


@router.post("/knowledge/{item_id}/status")
def change_knowledge_status(
    item_id: int,
    data: KnowledgeStatusUpdate,
    user: dict[str, Any] = Depends(current_user),
) -> dict[str, Any]:
    require_roles(user, {"admin"})
    return change_knowledge_status_record(item_id, data, user)


@router.delete("/knowledge/{item_id}")
def delete_knowledge(item_id: int, user: dict[str, Any] = Depends(current_user)) -> dict[str, Any]:
    require_roles(user, {"admin"})
    return delete_knowledge_record(item_id, user)
