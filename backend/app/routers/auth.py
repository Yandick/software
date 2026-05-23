from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Depends, HTTPException

from ..database import audit, get_user_by_username, update_user_password_hash
from ..deps import public_user
from ..schemas import LoginRequest
from ..security import create_access_token, current_user, hash_password, password_needs_rehash, verify_password

router = APIRouter(prefix="/api", tags=["auth"])


@router.post("/auth/login")
def login(data: LoginRequest) -> dict[str, Any]:
    user = get_user_by_username(data.username)
    if not user or not verify_password(data.password, user["password_hash"]):
        audit("login_failed", "user", f"登录失败：{data.username}")
        raise HTTPException(status_code=401, detail="用户名或密码错误")
    if password_needs_rehash(user["password_hash"]):
        update_user_password_hash(user["id"], hash_password(data.password))
        user = get_user_by_username(data.username) or user
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
