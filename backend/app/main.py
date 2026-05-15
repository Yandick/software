from __future__ import annotations

import json
from typing import Any

from fastapi import Depends, FastAPI, HTTPException, Query
from fastapi.middleware.cors import CORSMiddleware

from .config import get_settings
from .database import audit, connect, init_db, rows_to_dicts, utc_now
from .schemas import AccountCreate, AccountUpdate, IssueCreate, IssueHandle, IssueVisit, KnowledgeCreate, LoginRequest, QuestionRequest
from .security import create_access_token, current_user, verify_password
from .services.llm_service import llm_service
from .services.rag_service import RagService

app = FastAPI(title="运维数字员工系统")
rag_service = RagService()

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


@app.on_event("startup")
def startup() -> None:
    init_db()


def require_roles(user: dict[str, Any], roles: set[str]) -> None:
    if user.get("role") not in roles:
        raise HTTPException(status_code=403, detail="Permission denied")


def public_user(user: dict[str, Any]) -> dict[str, Any]:
    return {key: user[key] for key in ["id", "username", "real_name", "role", "department", "status"] if key in user}


@app.get("/api/health")
def health() -> dict[str, Any]:
    return {"status": "ok", "app": get_settings().app_name}


@app.post("/api/auth/login")
def login(data: LoginRequest) -> dict[str, Any]:
    from .database import get_user_by_username

    user = get_user_by_username(data.username)
    if not user or not verify_password(data.password, user["password_hash"]):
        audit("login_failed", "user", f"登录失败：{data.username}")
        raise HTTPException(status_code=401, detail="用户名或密码错误")
    token = create_access_token(user["username"], {"role": user["role"]})
    audit("login", "user", f"用户登录：{user['username']}", user["id"])
    return {"access_token": token, "token_type": "bearer", "user": public_user(user)}


@app.get("/api/auth/me")
def me(user: dict[str, Any] = Depends(current_user)) -> dict[str, Any]:
    return public_user(user)


@app.post("/api/auth/refresh")
def refresh(user: dict[str, Any] = Depends(current_user)) -> dict[str, Any]:
    return {"data": create_access_token(user["username"], {"role": user["role"]}), "status": 0}


@app.get("/api/menu/all")
def menus(_: dict[str, Any] = Depends(current_user)) -> list[dict[str, Any]]:
    return []


@app.post("/api/qa/ask")
def ask(data: QuestionRequest, user: dict[str, Any] = Depends(current_user)) -> dict[str, Any]:
    result = rag_service.search(data.question)
    context = rag_service.build_context(result.references)
    model_result = llm_service.generate(data.question, context, data.enable_thinking)
    answer = model_result["content"] if model_result.get("ok") and model_result.get("content") else rag_service.fallback_answer(data.question, result)
    need_human = result.high_risk or result.confidence < 0.08 or not result.references
    if need_human:
        answer += "\n\n系统判断该问题建议转人工：请创建在线记录，补充影响范围、联系方式和错误截图/日志。"
    refs = [{"id": item["id"], "title": item["title"], "tags": item.get("tags", ""), "score": item.get("score", 0)} for item in result.references]
    with connect() as conn:
        conn.execute(
            "insert into qa_logs(question,answer,need_human,model_status,references_json,created_at) values(?,?,?,?,?,?)",
            (data.question, answer, int(need_human), model_result.get("status", "unknown"), json.dumps(refs, ensure_ascii=False), utc_now()),
        )
    return {"answer": answer, "references": refs, "need_human": need_human, "model_status": model_result.get("status", "unknown")}


@app.get("/api/qa/suggest")
def suggest(q: str = "", limit: int = Query(8, ge=1, le=20), user: dict[str, Any] = Depends(current_user)) -> list[dict[str, Any]]:
    return rag_service.suggest(q, limit)


@app.get("/api/knowledge")
def list_knowledge(q: str = "", user: dict[str, Any] = Depends(current_user)) -> list[dict[str, Any]]:
    with connect() as conn:
        if q:
            rows = conn.execute(
                "select * from knowledge where title like ? or content like ? or tags like ? order by id desc",
                (f"%{q}%", f"%{q}%", f"%{q}%"),
            ).fetchall()
        else:
            rows = conn.execute("select * from knowledge order by id desc").fetchall()
    return rows_to_dicts(rows)


@app.post("/api/knowledge")
def create_knowledge(data: KnowledgeCreate, user: dict[str, Any] = Depends(current_user)) -> dict[str, Any]:
    require_roles(user, {"admin", "ops"})
    now = utc_now()
    with connect() as conn:
        cur = conn.execute(
            "insert into knowledge(title,content,tags,source_type,status,created_at,updated_at) values(?,?,?,?,?,?,?)",
            (data.title, data.content, data.tags, data.source_type, data.status, now, now),
        )
        item_id = int(cur.lastrowid)
    audit("knowledge_create", "knowledge", f"新增知识：{data.title}", item_id)
    return {"id": item_id, **data.model_dump(), "created_at": now, "updated_at": now}


@app.put("/api/knowledge/{item_id}")
def update_knowledge(item_id: int, data: KnowledgeCreate, user: dict[str, Any] = Depends(current_user)) -> dict[str, Any]:
    require_roles(user, {"admin", "ops"})
    now = utc_now()
    with connect() as conn:
        conn.execute(
            "update knowledge set title=?,content=?,tags=?,source_type=?,status=?,updated_at=? where id=?",
            (data.title, data.content, data.tags, data.source_type, data.status, now, item_id),
        )
    audit("knowledge_update", "knowledge", f"更新知识：{data.title}", item_id)
    return {"id": item_id, **data.model_dump(), "updated_at": now}


@app.get("/api/issues")
def list_issues(status: str = "", user: dict[str, Any] = Depends(current_user)) -> list[dict[str, Any]]:
    with connect() as conn:
        if status:
            rows = conn.execute("select * from issues where status=? order by id desc", (status,)).fetchall()
        else:
            rows = conn.execute("select * from issues order by id desc").fetchall()
    return rows_to_dicts(rows)


@app.post("/api/issues")
def create_issue(data: IssueCreate, user: dict[str, Any] = Depends(current_user)) -> dict[str, Any]:
    now = utc_now()
    with connect() as conn:
        cur = conn.execute(
            "insert into issues(title,description,contact_phone,priority,status,created_at,updated_at) values(?,?,?,?,?,?,?)",
            (data.title, data.description, data.contact_phone, data.priority, "pending", now, now),
        )
        issue_id = int(cur.lastrowid)
    audit("issue_create", "issue", f"创建在线记录：{data.title}", issue_id)
    return {"id": issue_id, **data.model_dump(), "status": "pending", "created_at": now, "updated_at": now}


@app.post("/api/issues/{issue_id}/handle")
def handle_issue(issue_id: int, data: IssueHandle, user: dict[str, Any] = Depends(current_user)) -> dict[str, Any]:
    require_roles(user, {"admin", "ops"})
    now = utc_now()
    with connect() as conn:
        conn.execute("update issues set solution=?,status='handled',updated_at=? where id=?", (data.solution, now, issue_id))
    audit("issue_handle", "issue", f"处理在线记录：{data.solution[:80]}", issue_id)
    return {"id": issue_id, "status": "handled", "solution": data.solution}


@app.post("/api/issues/{issue_id}/visit")
def visit_issue(issue_id: int, data: IssueVisit, user: dict[str, Any] = Depends(current_user)) -> dict[str, Any]:
    require_roles(user, {"admin", "ops"})
    now = utc_now()
    status = "closed" if data.resolved else "pending"
    with connect() as conn:
        conn.execute(
            "update issues set resolved=?,satisfaction_score=?,visit_result=?,status=?,updated_at=? where id=?",
            (int(data.resolved), data.satisfaction_score, data.visit_result, status, now, issue_id),
        )
        row = conn.execute("select title,solution from issues where id=?", (issue_id,)).fetchone()
        if data.resolved and row and row["solution"]:
            conn.execute(
                "insert into knowledge(title,content,tags,source_type,status,created_at,updated_at) values(?,?,?,?,?,?,?)",
                (f"处理案例：{row['title']}", row["solution"], "处理案例,回访已解决", "case", "published", now, now),
            )
    audit("issue_visit", "issue", f"回访：{'已解决' if data.resolved else '未解决'} {data.visit_result}", issue_id)
    return {"id": issue_id, "status": status, "resolved": data.resolved}


@app.get("/api/accounts")
def list_accounts(q: str = "", user: dict[str, Any] = Depends(current_user)) -> list[dict[str, Any]]:
    require_roles(user, {"admin", "ops", "auditor"})
    with connect() as conn:
        if q:
            rows = conn.execute("select * from ops_accounts where account_name like ? order by id desc", (f"%{q}%",)).fetchall()
        else:
            rows = conn.execute("select * from ops_accounts order by id desc").fetchall()
    return rows_to_dicts(rows)


@app.post("/api/accounts")
def create_account(data: AccountCreate, user: dict[str, Any] = Depends(current_user)) -> dict[str, Any]:
    require_roles(user, {"admin"})
    now = utc_now()
    with connect() as conn:
        cur = conn.execute(
            "insert into ops_accounts(account_name,permission_scope,status,remark,created_at,updated_at) values(?,?,?,?,?,?)",
            (data.account_name, data.permission_scope, "active", data.remark, now, now),
        )
        account_id = int(cur.lastrowid)
    audit("account_create", "ops_account", f"新增运维账号：{data.account_name}", account_id)
    return {"id": account_id, **data.model_dump(), "status": "active", "created_at": now, "updated_at": now}


@app.put("/api/accounts/{account_id}")
def update_account(account_id: int, data: AccountUpdate, user: dict[str, Any] = Depends(current_user)) -> dict[str, Any]:
    require_roles(user, {"admin"})
    now = utc_now()
    fields = {k: v for k, v in data.model_dump().items() if v is not None}
    if not fields:
        return {"id": account_id}
    assignments = ",".join(f"{key}=?" for key in fields)
    values = list(fields.values()) + [now, account_id]
    with connect() as conn:
        conn.execute(f"update ops_accounts set {assignments},updated_at=? where id=?", values)
    audit("account_update", "ops_account", f"修改运维账号：{fields}", account_id)
    return {"id": account_id, **fields, "updated_at": now}


@app.post("/api/accounts/{account_id}/freeze")
def freeze_account(account_id: int, user: dict[str, Any] = Depends(current_user)) -> dict[str, Any]:
    require_roles(user, {"admin"})
    now = utc_now()
    with connect() as conn:
        conn.execute("update ops_accounts set status='frozen',updated_at=? where id=?", (now, account_id))
    audit("account_freeze", "ops_account", "冻结运维账号", account_id)
    return {"id": account_id, "status": "frozen"}


@app.post("/api/accounts/{account_id}/unfreeze")
def unfreeze_account(account_id: int, user: dict[str, Any] = Depends(current_user)) -> dict[str, Any]:
    require_roles(user, {"admin"})
    now = utc_now()
    with connect() as conn:
        conn.execute("update ops_accounts set status='active',updated_at=? where id=?", (now, account_id))
    audit("account_unfreeze", "ops_account", "解冻运维账号", account_id)
    return {"id": account_id, "status": "active"}


@app.get("/api/audit/logs")
def audit_logs(limit: int = Query(100, le=500), user: dict[str, Any] = Depends(current_user)) -> dict[str, Any]:
    require_roles(user, {"admin", "auditor"})
    with connect() as conn:
        audit_rows = conn.execute("select * from audit_logs order by id desc limit ?", (limit,)).fetchall()
        qa_rows = conn.execute("select * from qa_logs order by id desc limit ?", (limit,)).fetchall()
    return {"audit": rows_to_dicts(audit_rows), "qa": rows_to_dicts(qa_rows)}


@app.get("/api/audit/stats")
def stats(user: dict[str, Any] = Depends(current_user)) -> dict[str, Any]:
    with connect() as conn:
        total_qa = conn.execute("select count(*) from qa_logs").fetchone()[0]
        human = conn.execute("select count(*) from qa_logs where need_human=1").fetchone()[0]
        issues = conn.execute("select count(*) from issues").fetchone()[0]
        accounts = conn.execute("select count(*) from ops_accounts").fetchone()[0]
        knowledge = conn.execute("select count(*) from knowledge").fetchone()[0]
        closed = conn.execute("select count(*) from issues where status='closed'").fetchone()[0]
    return {
        "total_qa": total_qa,
        "human_transfer_rate": human / total_qa if total_qa else 0,
        "self_solved_rate": 1 - (human / total_qa) if total_qa else 0,
        "issues": issues,
        "accounts": accounts,
        "knowledge": knowledge,
        "closed_issues": closed,
    }
