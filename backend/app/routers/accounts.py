from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Depends

from ..deps import require_roles
from ..schemas import AccountApprovalCreate, AccountApprovalDecision, AccountCreate, AccountUpdate
from ..security import current_user
from ..services.accounts_service import (
    create_account as create_account_record,
    create_account_approval as create_account_approval_record,
    decide_account_approval as decide_account_approval_record,
    export_accounts as export_account_rows,
    list_account_approvals as list_account_approval_rows,
    list_accounts as list_account_rows,
    request_account_freeze,
    request_account_unfreeze,
    request_account_update,
)

router = APIRouter(prefix="/api", tags=["accounts"])


@router.get("/accounts")
def list_accounts(q: str = "", user: dict[str, Any] = Depends(current_user)) -> list[dict[str, Any]]:
    require_roles(user, {"admin", "ops", "auditor"})
    return list_account_rows(q)


@router.get("/accounts/export")
def export_accounts(q: str = "", user: dict[str, Any] = Depends(current_user)) -> dict[str, Any]:
    require_roles(user, {"admin", "auditor"})
    return export_account_rows(q)


@router.post("/accounts")
def create_account(data: AccountCreate, user: dict[str, Any] = Depends(current_user)) -> dict[str, Any]:
    require_roles(user, {"admin"})
    return create_account_record(data)


@router.put("/accounts/{account_id}")
def update_account(account_id: int, data: AccountUpdate, user: dict[str, Any] = Depends(current_user)) -> dict[str, Any]:
    require_roles(user, {"admin", "ops"})
    return request_account_update(account_id, data, user)


@router.post("/accounts/{account_id}/freeze")
def freeze_account(account_id: int, user: dict[str, Any] = Depends(current_user)) -> dict[str, Any]:
    require_roles(user, {"admin", "ops"})
    return request_account_freeze(account_id, user)


@router.post("/accounts/{account_id}/unfreeze")
def unfreeze_account(account_id: int, user: dict[str, Any] = Depends(current_user)) -> dict[str, Any]:
    require_roles(user, {"admin", "ops"})
    return request_account_unfreeze(account_id, user)


@router.get("/account-approvals")
def list_account_approvals(status: str = "", user: dict[str, Any] = Depends(current_user)) -> list[dict[str, Any]]:
    require_roles(user, {"admin", "ops", "auditor"})
    return list_account_approval_rows(status)


@router.post("/account-approvals")
def create_account_approval(data: AccountApprovalCreate, user: dict[str, Any] = Depends(current_user)) -> dict[str, Any]:
    require_roles(user, {"admin", "ops"})
    return create_account_approval_record(data, user)


@router.post("/account-approvals/{approval_id}/decision")
def decide_account_approval(
    approval_id: int,
    data: AccountApprovalDecision,
    user: dict[str, Any] = Depends(current_user),
) -> dict[str, Any]:
    require_roles(user, {"admin"})
    return decide_account_approval_record(approval_id, data, user)
