from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Depends, File, UploadFile
from fastapi.responses import FileResponse

from ..deps import require_roles
from ..schemas import IssueCreate, IssueDraftRequest, IssueFeedback, IssueHandle, IssueStatusUpdate, IssueVisit
from ..security import current_user
from ..services.issues_service import (
    accept_issue as accept_issue_record,
    assist_issue as assist_issue_record,
    change_issue_status as change_issue_status_record,
    create_issue as create_issue_record,
    create_issue_knowledge_candidate as create_issue_knowledge_candidate_record,
    download_issue_attachment as download_issue_attachment_record,
    draft_issue as draft_issue_record,
    feedback_issue as feedback_issue_record,
    handle_issue as handle_issue_record,
    list_issues as list_issue_rows,
    upload_issue_attachment as upload_issue_attachment_record,
    visit_issue as visit_issue_record,
)

router = APIRouter(prefix="/api", tags=["issues"])


@router.post("/issues/draft")
def draft_issue(data: IssueDraftRequest, user: dict[str, Any] = Depends(current_user)) -> dict[str, Any]:
    return draft_issue_record(data)


@router.post("/issues/attachments")
def upload_issue_attachment(file: UploadFile = File(...), user: dict[str, Any] = Depends(current_user)) -> dict[str, Any]:
    return upload_issue_attachment_record(file, user)


@router.get("/issues/attachments/{attachment_id}/download")
def download_issue_attachment(attachment_id: int, user: dict[str, Any] = Depends(current_user)) -> FileResponse:
    return download_issue_attachment_record(attachment_id, user)


@router.get("/issues")
def list_issues(status: str = "", q: str = "", user: dict[str, Any] = Depends(current_user)) -> list[dict[str, Any]]:
    return list_issue_rows(status, q, user)


@router.post("/issues")
def create_issue(data: IssueCreate, user: dict[str, Any] = Depends(current_user)) -> dict[str, Any]:
    return create_issue_record(data, user)


@router.post("/issues/{issue_id}/accept")
def accept_issue(issue_id: int, user: dict[str, Any] = Depends(current_user)) -> dict[str, Any]:
    require_roles(user, {"admin", "ops"})
    return accept_issue_record(issue_id, user)


@router.post("/issues/{issue_id}/status")
def change_issue_status(
    issue_id: int,
    data: IssueStatusUpdate,
    user: dict[str, Any] = Depends(current_user),
) -> dict[str, Any]:
    require_roles(user, {"admin", "ops"})
    return change_issue_status_record(issue_id, data, user)


@router.post("/issues/{issue_id}/handle")
def handle_issue(issue_id: int, data: IssueHandle, user: dict[str, Any] = Depends(current_user)) -> dict[str, Any]:
    require_roles(user, {"admin", "ops"})
    return handle_issue_record(issue_id, data, user)


@router.post("/issues/{issue_id}/visit")
def visit_issue(issue_id: int, data: IssueVisit, user: dict[str, Any] = Depends(current_user)) -> dict[str, Any]:
    require_roles(user, {"admin", "ops"})
    return visit_issue_record(issue_id, data, user)


@router.post("/issues/{issue_id}/knowledge-candidate")
def create_issue_knowledge_candidate(issue_id: int, user: dict[str, Any] = Depends(current_user)) -> dict[str, Any]:
    require_roles(user, {"admin", "ops"})
    return create_issue_knowledge_candidate_record(issue_id, user)


@router.post("/issues/{issue_id}/feedback")
def feedback_issue(issue_id: int, data: IssueFeedback, user: dict[str, Any] = Depends(current_user)) -> dict[str, Any]:
    return feedback_issue_record(issue_id, data, user)


@router.get("/issues/{issue_id}/assist")
def assist_issue(issue_id: int, user: dict[str, Any] = Depends(current_user)) -> dict[str, Any]:
    require_roles(user, {"admin", "ops"})
    return assist_issue_record(issue_id)
