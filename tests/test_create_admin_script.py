from __future__ import annotations

import json
import os
import sqlite3
import subprocess
import sys
from pathlib import Path

from backend.app.passwords import verify_password

ROOT_DIR = Path(__file__).resolve().parents[1]


def run_create_admin(db_path: Path, *args: str, admin_password: str | None = None) -> subprocess.CompletedProcess[str]:
    env = os.environ.copy()
    env.update(
        {
            "OPS_DATABASE_URL": f"sqlite:///{db_path}",
            "OPS_ENVIRONMENT": "production",
            "OPS_JWT_SECRET": "x" * 40,
            "OPS_SEED_DEMO_ACCOUNTS": "false",
        }
    )
    env.pop("OPS_ADMIN_PASSWORD", None)
    if admin_password is not None:
        env["OPS_ADMIN_PASSWORD"] = admin_password
    return subprocess.run(
        [sys.executable, "scripts/create_admin.py", *args],
        cwd=ROOT_DIR,
        env=env,
        capture_output=True,
        text=True,
        timeout=120,
    )


def fetch_user(db_path: Path, username: str) -> sqlite3.Row | None:
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    try:
        return conn.execute("select * from users where username=?", (username,)).fetchone()
    finally:
        conn.close()


def count_rows(db_path: Path, table: str) -> int:
    conn = sqlite3.connect(db_path)
    try:
        return int(conn.execute(f"select count(*) from {table}").fetchone()[0])
    finally:
        conn.close()


def test_create_admin_script_creates_admin_without_demo_seed(tmp_path: Path) -> None:
    db_path = tmp_path / "prod.db"

    result = run_create_admin(
        db_path,
        "--username",
        "platform_admin",
        "--real-name",
        "Platform Admin",
        "--department",
        "IT",
        admin_password="StrongPassword123!",
    )

    assert result.returncode == 0, result.stderr
    payload = json.loads(result.stdout)
    assert payload["ok"] is True
    assert payload["user"]["created"] is True

    user = fetch_user(db_path, "platform_admin")
    assert user is not None
    assert user["role"] == "admin"
    assert user["status"] == "active"
    assert user["password_hash"].startswith("$argon2id$")
    assert verify_password("StrongPassword123!", user["password_hash"])
    assert count_rows(db_path, "users") == 1
    assert count_rows(db_path, "knowledge") > 0


def test_create_admin_script_does_not_overwrite_existing_user_without_replace(tmp_path: Path) -> None:
    db_path = tmp_path / "prod.db"
    first = run_create_admin(db_path, "--username", "platform_admin", admin_password="InitialPassword123!")
    second = run_create_admin(db_path, "--username", "platform_admin", admin_password="RotatedPassword123!")

    assert first.returncode == 0, first.stderr
    assert second.returncode == 1
    assert "already exists" in second.stderr

    user = fetch_user(db_path, "platform_admin")
    assert user is not None
    assert verify_password("InitialPassword123!", user["password_hash"])
    assert not verify_password("RotatedPassword123!", user["password_hash"])
    assert count_rows(db_path, "users") == 1


def test_create_admin_script_can_rotate_existing_user_with_replace(tmp_path: Path) -> None:
    db_path = tmp_path / "prod.db"
    first = run_create_admin(db_path, "--username", "platform_admin", admin_password="InitialPassword123!")
    second = run_create_admin(
        db_path,
        "--username",
        "platform_admin",
        "--replace",
        "--real-name",
        "Rotated Admin",
        "--department",
        "Security",
        admin_password="RotatedPassword123!",
    )

    assert first.returncode == 0, first.stderr
    assert second.returncode == 0, second.stderr
    payload = json.loads(second.stdout)
    assert payload["user"]["created"] is False

    user = fetch_user(db_path, "platform_admin")
    assert user is not None
    assert user["real_name"] == "Rotated Admin"
    assert user["department"] == "Security"
    assert verify_password("RotatedPassword123!", user["password_hash"])
    assert count_rows(db_path, "users") == 1


def test_create_admin_script_rejects_password_argument_in_production(tmp_path: Path) -> None:
    db_path = tmp_path / "prod.db"

    result = run_create_admin(
        db_path,
        "--username",
        "platform_admin",
        "--password",
        "StrongPassword123!",
    )

    assert result.returncode == 1
    assert "do not pass production admin passwords via --password" in result.stderr
