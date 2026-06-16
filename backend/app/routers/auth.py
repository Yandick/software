from __future__ import annotations

import time
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Request

from ..database import audit, get_user_by_username, update_user_password_hash
from ..deps import public_user
from ..schemas import LoginRequest
from ..security import create_access_token, current_user, hash_password, password_needs_rehash, verify_password

router = APIRouter(prefix="/api", tags=["auth"])

LOGIN_FAILURE_WINDOW_SECONDS = 300
LOGIN_FAILURE_LIMIT = 10
_LOGIN_FAILURES: dict[str, list[float]] = {}


def check_login_rate_limit(username: str) -> None:
    now = time.monotonic()
    attempts = [
        item
        for item in _LOGIN_FAILURES.get(username, [])
        if now - item < LOGIN_FAILURE_WINDOW_SECONDS
    ]
    _LOGIN_FAILURES[username] = attempts
    if len(attempts) >= LOGIN_FAILURE_LIMIT:
        raise HTTPException(status_code=429, detail="登录失败次数过多，请稍后再试")


def record_login_failure(username: str) -> None:
    attempts = _LOGIN_FAILURES.setdefault(username, [])
    attempts.append(time.monotonic())


@router.post("/auth/login")
def login(data: LoginRequest, request: Request) -> dict[str, Any]:
    username = data.username.strip()
    if not username:
        raise HTTPException(status_code=400, detail="用户名不能为空")
    check_login_rate_limit(username)
    user = get_user_by_username(username)
    if not user or not verify_password(data.password, user["password_hash"]):
        record_login_failure(username)
        audit("login_failed", "user", f"登录失败：{username} from {request.client.host if request.client else 'unknown'}")
        raise HTTPException(status_code=401, detail="用户名或密码错误")
    if user.get("status") != "active":
        record_login_failure(username)
        audit("login_denied", "user", f"禁用账号登录被拒绝：{username}", user["id"])
        raise HTTPException(status_code=403, detail="账号已停用，请联系管理员")
    if password_needs_rehash(user["password_hash"]):
        update_user_password_hash(user["id"], hash_password(data.password))
        user = get_user_by_username(username) or user
    _LOGIN_FAILURES.pop(username, None)
    token = create_access_token(user["username"], {"role": user["role"]})
    audit("login", "user", f"用户登录：{user['username']}", user["id"])
    return {"access_token": token, "token_type": "bearer", "user": public_user(user)}


@router.get("/auth/me")
def me(user: dict[str, Any] = Depends(current_user)) -> dict[str, Any]:
    return public_user(user)


@router.post("/auth/refresh")
def refresh(user: dict[str, Any] = Depends(current_user)) -> dict[str, Any]:
    return {"data": create_access_token(user["username"], {"role": user["role"]}), "status": 0}


@router.get("/menu/all")
def menus(_: dict[str, Any] = Depends(current_user)) -> list[dict[str, Any]]:
    return []
