from __future__ import annotations

import csv
import io
import json
from datetime import datetime
from typing import Any

from fastapi import HTTPException

from ..database import audit, connect, rows_to_dicts, utc_now, write_audit
from ..deps import ensure_row_exists
from ..schemas import AccountApprovalCreate, AccountApprovalDecision, AccountCreate, AccountUpdate

ACCOUNT_RISK_LEVELS = {"high", "low", "medium"}
ACCOUNT_UPDATE_FIELDS = {
    "contact_phone",
    "department",
    "expires_at",
    "owner_name",
    "permission_scope",
    "remark",
    "risk_level",
    "status",
}
CSV_DANGEROUS_PREFIXES = ("=", "+", "-", "@")


def normalize_account_payload(action: str, payload: dict[str, Any]) -> dict[str, Any]:
    if action != "update":
        return {}
    fields = {key: value for key, value in payload.items() if key in ACCOUNT_UPDATE_FIELDS and value is not None}
    if "status" in fields and fields["status"] not in {"active", "frozen"}:
        raise HTTPException(status_code=400, detail="账号状态只能是 active 或 frozen")
    if "risk_level" in fields and fields["risk_level"] not in ACCOUNT_RISK_LEVELS:
        raise HTTPException(status_code=400, detail="账号风险等级只能是 low、medium 或 high")
    return fields


def validate_account_create(data: AccountCreate) -> None:
    if data.risk_level not in ACCOUNT_RISK_LEVELS:
        raise HTTPException(status_code=400, detail="账号风险等级只能是 low、medium 或 high")


def account_expiry_meta(expires_at: str) -> dict[str, Any]:
    if not expires_at:
        return {"days_to_expire": None, "expiry_status": "none"}
    try:
        expire_date = datetime.strptime(expires_at[:10], "%Y-%m-%d").date()
    except ValueError:
        return {"days_to_expire": None, "expiry_status": "invalid"}
    days = (expire_date - datetime.utcnow().date()).days
    if days < 0:
        status = "expired"
    elif days <= 30:
        status = "expiring"
    else:
        status = "valid"
    return {"days_to_expire": days, "expiry_status": status}


def account_row_to_dict(row: Any) -> dict[str, Any]:
    item = dict(row)
    item.update(account_expiry_meta(item.get("expires_at", "")))
    return item


def safe_csv_value(value: Any) -> Any:
    if not isinstance(value, str):
        return value
    visible_value = value.lstrip(" \t\r\n")
    if visible_value and visible_value[0] in CSV_DANGEROUS_PREFIXES:
        return f"'{value}"
    return value


def fetch_account_rows(conn: Any, q: str = "") -> list[Any]:
    if q:
        return conn.execute(
            """
            select * from ops_accounts
            where account_name like ?
               or owner_name like ?
               or department like ?
               or contact_phone like ?
               or permission_scope like ?
               or risk_level like ?
               or remark like ?
            order by id desc
            """,
            (f"%{q}%", f"%{q}%", f"%{q}%", f"%{q}%", f"%{q}%", f"%{q}%", f"%{q}%"),
        ).fetchall()
    return conn.execute("select * from ops_accounts order by id desc").fetchall()


def list_accounts(q: str = "") -> list[dict[str, Any]]:
    with connect() as conn:
        rows = fetch_account_rows(conn, q)
    return [account_row_to_dict(row) for row in rows]


def build_accounts_csv(accounts: list[dict[str, Any]]) -> str:
    output = io.StringIO()
    output.write("\ufeff")
    fields = [
        ("id", "ID"),
        ("account_name", "账号名"),
        ("owner_name", "负责人"),
        ("department", "部门"),
        ("contact_phone", "联系方式"),
        ("permission_scope", "权限范围"),
        ("status", "状态"),
        ("risk_level", "风险等级"),
        ("expires_at", "有效期"),
        ("expiry_status", "到期状态"),
        ("days_to_expire", "剩余天数"),
        ("remark", "备注"),
        ("created_at", "创建时间"),
        ("updated_at", "更新时间"),
    ]
    writer = csv.DictWriter(output, fieldnames=[key for key, _ in fields], extrasaction="ignore")
    writer.writerow({key: label for key, label in fields})
    for account in accounts:
        writer.writerow({key: safe_csv_value(value) for key, value in account.items()})
    return output.getvalue()


def export_accounts(q: str = "") -> dict[str, Any]:
    accounts = list_accounts(q)
    audit("account_export", "ops_account", f"导出运维账号 CSV：{len(accounts)} 条，查询条件：{q or '全部'}")
    return {
        "content": build_accounts_csv(accounts),
        "count": len(accounts),
        "filename": f"ops_accounts_{datetime.utcnow().strftime('%Y%m%d%H%M%S')}.csv",
    }


def create_account(data: AccountCreate) -> dict[str, Any]:
    validate_account_create(data)
    now = utc_now()
    with connect() as conn:
        existing = conn.execute("select id from ops_accounts where account_name=?", (data.account_name,)).fetchone()
        if existing:
            raise HTTPException(status_code=409, detail="账号名已存在")
        cur = conn.execute(
            """
            insert into ops_accounts(
              account_name,owner_name,department,contact_phone,permission_scope,status,risk_level,expires_at,remark,created_at,updated_at
            ) values(?,?,?,?,?,?,?,?,?,?,?)
            """,
            (
                data.account_name,
                data.owner_name,
                data.department,
                data.contact_phone,
                data.permission_scope,
                "active",
                data.risk_level,
                data.expires_at,
                data.remark,
                now,
                now,
            ),
        )
        account_id = int(cur.lastrowid)
    audit("account_create", "ops_account", f"新增运维账号：{data.account_name}", account_id)
    return {"id": account_id, **data.model_dump(), "status": "active", "created_at": now, "updated_at": now}


def create_account_approval_record(
    conn: Any,
    account_id: int,
    action: str,
    payload: dict[str, Any],
    reason: str,
    user: dict[str, Any],
) -> int:
    if action not in {"freeze", "unfreeze", "update"}:
        raise HTTPException(status_code=400, detail="审批动作只能是 freeze、unfreeze 或 update")
    row = conn.execute("select id from ops_accounts where id=?", (account_id,)).fetchone()
    ensure_row_exists(row, "运维账号")
    safe_payload = normalize_account_payload(action, payload)
    now = utc_now()
    cur = conn.execute(
        """
        insert into account_approvals(account_id,action,payload_json,reason,status,requested_by,created_at,updated_at)
        values(?,?,?,?,?,?,?,?)
        """,
        (account_id, action, json.dumps(safe_payload, ensure_ascii=False), reason, "pending", user.get("id"), now, now),
    )
    approval_id = int(cur.lastrowid)
    write_audit(conn, "account_approval_create", "account_approval", f"创建账号审批：{action} account={account_id}", approval_id)
    return approval_id


def apply_account_action(conn: Any, approval: dict[str, Any], operator: dict[str, Any]) -> None:
    now = utc_now()
    account_id = int(approval["account_id"])
    action = approval["action"]
    payload = json.loads(approval.get("payload_json") or "{}")
    row = conn.execute("select account_name,status from ops_accounts where id=?", (account_id,)).fetchone()
    ensure_row_exists(row, "运维账号")
    if action == "freeze":
        if row["status"] == "frozen":
            raise HTTPException(status_code=400, detail="账号已冻结")
        conn.execute("update ops_accounts set status='frozen',updated_at=? where id=?", (now, account_id))
        write_audit(conn, "account_freeze", "ops_account", f"{operator.get('real_name', '')} 审批后冻结运维账号：{row['account_name']}", account_id)
    elif action == "unfreeze":
        if row["status"] == "active":
            raise HTTPException(status_code=400, detail="账号已是启用状态")
        conn.execute("update ops_accounts set status='active',updated_at=? where id=?", (now, account_id))
        write_audit(conn, "account_unfreeze", "ops_account", f"{operator.get('real_name', '')} 审批后解冻运维账号：{row['account_name']}", account_id)
    elif action == "update":
        fields = normalize_account_payload(action, payload)
        if fields:
            assignments = ",".join(f"{key}=?" for key in fields)
            conn.execute(f"update ops_accounts set {assignments},updated_at=? where id=?", [*fields.values(), now, account_id])
            write_audit(conn, "account_update", "ops_account", f"{operator.get('real_name', '')} 审批后修改运维账号：{fields}", account_id)
    else:
        raise HTTPException(status_code=400, detail="不支持的账号审批动作")


def request_account_update(account_id: int, data: AccountUpdate, user: dict[str, Any]) -> dict[str, Any]:
    fields = {key: value for key, value in data.model_dump().items() if value is not None}
    fields = normalize_account_payload("update", fields)
    if not fields:
        return {"id": account_id}
    with connect() as conn:
        approval_id = create_account_approval_record(conn, account_id, "update", fields, "申请修改运维账号信息", user)
    return {"id": account_id, "approval_id": approval_id, "status": "pending_approval"}


def request_account_freeze(account_id: int, user: dict[str, Any]) -> dict[str, Any]:
    with connect() as conn:
        row = conn.execute("select status from ops_accounts where id=?", (account_id,)).fetchone()
        ensure_row_exists(row, "运维账号")
        if row["status"] == "frozen":
            raise HTTPException(status_code=400, detail="账号已冻结")
        approval_id = create_account_approval_record(conn, account_id, "freeze", {}, "申请冻结运维账号", user)
    return {"id": account_id, "approval_id": approval_id, "status": "pending_approval"}


def request_account_unfreeze(account_id: int, user: dict[str, Any]) -> dict[str, Any]:
    with connect() as conn:
        row = conn.execute("select status from ops_accounts where id=?", (account_id,)).fetchone()
        ensure_row_exists(row, "运维账号")
        if row["status"] == "active":
            raise HTTPException(status_code=400, detail="账号已是启用状态")
        approval_id = create_account_approval_record(conn, account_id, "unfreeze", {}, "申请解冻运维账号", user)
    return {"id": account_id, "approval_id": approval_id, "status": "pending_approval"}


def list_account_approvals(status: str = "") -> list[dict[str, Any]]:
    with connect() as conn:
        params: list[Any] = []
        where = ""
        if status:
            where = "where aa.status=?"
            params.append(status)
        rows = conn.execute(
            f"""
            select aa.*, oa.account_name, ru.real_name as requester_name, au.real_name as approver_name
            from account_approvals aa
            left join ops_accounts oa on oa.id = aa.account_id
            left join users ru on ru.id = aa.requested_by
            left join users au on au.id = aa.approved_by
            {where}
            order by aa.id desc
            """,
            params,
        ).fetchall()
    return rows_to_dicts(rows)


def create_account_approval(data: AccountApprovalCreate, user: dict[str, Any]) -> dict[str, Any]:
    with connect() as conn:
        approval_id = create_account_approval_record(conn, data.account_id, data.action, data.payload, data.reason, user)
    return {"id": approval_id, "status": "pending"}


def decide_account_approval(approval_id: int, data: AccountApprovalDecision, user: dict[str, Any]) -> dict[str, Any]:
    now = utc_now()
    with connect() as conn:
        row = conn.execute("select * from account_approvals where id=?", (approval_id,)).fetchone()
        ensure_row_exists(row, "账号审批")
        approval = dict(row)
        if approval["status"] != "pending":
            raise HTTPException(status_code=400, detail="审批已处理")
        if approval.get("requested_by") == user.get("id"):
            raise HTTPException(status_code=403, detail="账号审批不能由申请人自己审批")
        if data.decision == "approved":
            apply_account_action(conn, approval, user)
        conn.execute(
            "update account_approvals set status=?,decision_reason=?,approved_by=?,decided_at=?,updated_at=? where id=?",
            (data.decision, data.reason, user.get("id"), now, now, approval_id),
        )
        write_audit(conn, "account_approval_decision", "account_approval", f"账号审批{data.decision}：{approval['action']} account={approval['account_id']}", approval_id)
    return {"id": approval_id, "status": data.decision}
