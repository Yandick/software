from __future__ import annotations

from typing import Callable

from fastapi.testclient import TestClient


def test_auth_login_me_refresh_and_menu(
    client: TestClient,
    auth_headers: Callable[[str, str], dict[str, str]],
) -> None:
    headers = auth_headers("admin", "admin123")

    me = client.get("/api/auth/me", headers=headers)
    assert me.status_code == 200, me.text
    assert me.json()["username"] == "admin"
    assert me.json()["role"] == "admin"

    refresh = client.post("/api/auth/refresh", headers=headers)
    assert refresh.status_code == 200, refresh.text
    assert refresh.json()["status"] == 0
    assert refresh.json()["data"]

    menu = client.get("/api/menu/all", headers=headers)
    assert menu.status_code == 200, menu.text
    assert menu.json() == []


def test_auth_rejects_bad_password(client: TestClient) -> None:
    response = client.post("/api/auth/login", json={"password": "wrong", "username": "admin"})
    assert response.status_code == 401, response.text
    assert response.json()["detail"] == "用户名或密码错误"


def test_auth_rejects_inactive_user(client: TestClient) -> None:
    from backend.app.database import _hash_password, connect, utc_now

    with connect() as conn:
        conn.execute(
            """
            insert into users(username,password_hash,real_name,role,department,status,created_at)
            values(?,?,?,?,?,?,?)
            """,
            ("inactive_user", _hash_password("inactive123"), "停用用户", "user", "业务部门", "frozen", utc_now()),
        )

    response = client.post("/api/auth/login", json={"password": "inactive123", "username": "inactive_user"})
    assert response.status_code == 403, response.text
    assert response.json()["detail"] == "账号已停用，请联系管理员"
