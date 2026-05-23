from __future__ import annotations

import hashlib

from fastapi.testclient import TestClient

from backend.app.database import connect, utc_now
from backend.app.passwords import hash_password, is_legacy_password_hash, password_needs_rehash, verify_password


def test_password_hashing_supports_argon2id_and_legacy_sha256() -> None:
    legacy_hash = hashlib.sha256("secret123".encode("utf-8")).hexdigest()
    assert is_legacy_password_hash(legacy_hash)
    assert verify_password("secret123", legacy_hash)
    assert password_needs_rehash(legacy_hash)

    modern_hash = hash_password("secret123")
    assert modern_hash.startswith("$argon2id$")
    assert verify_password("secret123", modern_hash)
    assert not is_legacy_password_hash(modern_hash)
    assert not verify_password("wrong", modern_hash)


def test_login_upgrades_legacy_password_hash(client: TestClient) -> None:
    legacy_hash = hashlib.sha256("legacy123".encode("utf-8")).hexdigest()
    with connect() as conn:
        conn.execute(
            "insert into users(username,password_hash,real_name,role,department,created_at) values(?,?,?,?,?,?)",
            ("legacy_user", legacy_hash, "旧哈希用户", "user", "业务部门", utc_now()),
        )

    response = client.post("/api/auth/login", json={"password": "legacy123", "username": "legacy_user"})
    assert response.status_code == 200, response.text
    with connect() as conn:
        row = conn.execute("select password_hash from users where username=?", ("legacy_user",)).fetchone()
    assert row is not None
    assert row["password_hash"].startswith("$argon2id$")
    assert verify_password("legacy123", row["password_hash"])


def test_seeded_demo_user_uses_argon2id(client: TestClient) -> None:
    with connect() as conn:
        row = conn.execute("select password_hash from users where username='user'").fetchone()
    assert row is not None
    assert row["password_hash"].startswith("$argon2id$")
    assert verify_password("user123", row["password_hash"])
