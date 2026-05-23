#!/usr/bin/env python3
from __future__ import annotations

import argparse
import getpass
import json
import os
import sys
from pathlib import Path
from typing import Any

ROOT_DIR = Path(__file__).resolve().parents[1]
if str(ROOT_DIR) not in sys.path:
    sys.path.insert(0, str(ROOT_DIR))

from backend.app.config import get_settings, validate_production_settings
from backend.app.database import connect, init_db, utc_now, write_audit
from backend.app.passwords import hash_password

MIN_PASSWORD_LENGTH = 12


def resolve_password(args: argparse.Namespace) -> str:
    if args.password:
        return args.password
    env_password = os.getenv(args.password_env)
    if env_password:
        return env_password

    password = getpass.getpass("Admin password: ")
    confirm = getpass.getpass("Confirm password: ")
    if password != confirm:
        raise RuntimeError("passwords do not match")
    return password


def validate_admin_input(username: str, password: str, real_name: str, department: str) -> None:
    if not username.strip():
        raise RuntimeError("username is required")
    if not real_name.strip():
        raise RuntimeError("real name is required")
    if not department.strip():
        raise RuntimeError("department is required")
    if len(password) < MIN_PASSWORD_LENGTH:
        raise RuntimeError(f"password must be at least {MIN_PASSWORD_LENGTH} characters")


def create_admin_user(
    *,
    username: str,
    password: str,
    real_name: str,
    department: str,
    replace: bool = False,
) -> dict[str, Any]:
    validate_admin_input(username, password, real_name, department)
    init_db()

    password_hash = hash_password(password)
    now = utc_now()
    with connect() as conn:
        row = conn.execute("select id from users where username=?", (username,)).fetchone()
        if row and not replace:
            raise RuntimeError(f"user {username!r} already exists; use --replace to rotate this admin account")
        if row:
            user_id = int(row["id"])
            conn.execute(
                """
                update users
                set password_hash=?,real_name=?,role='admin',department=?,status='active'
                where id=?
                """,
                (password_hash, real_name, department, user_id),
            )
            write_audit(conn, "admin_bootstrap_update", "user", f"Bootstrap admin rotated: {username}", user_id)
            return {"created": False, "id": user_id, "role": "admin", "status": "active", "username": username}

        cur = conn.execute(
            """
            insert into users(username,password_hash,real_name,role,department,status,created_at)
            values(?,?,?,?,?,?,?)
            """,
            (username, password_hash, real_name, "admin", department, "active", now),
        )
        user_id = int(cur.lastrowid)
        write_audit(conn, "admin_bootstrap_create", "user", f"Bootstrap admin created: {username}", user_id)
        return {"created": True, "id": user_id, "role": "admin", "status": "active", "username": username}


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Create or rotate the first production admin account.")
    parser.add_argument("--username", default="admin", help="Admin username to create. Default: admin")
    parser.add_argument("--real-name", default="系统管理员", help="Display name for the admin account")
    parser.add_argument("--department", default="信息技术部", help="Department for the admin account")
    parser.add_argument("--password", default="", help="Admin password for automation; prefer prompt or env in production")
    parser.add_argument("--password-env", default="OPS_ADMIN_PASSWORD", help="Environment variable to read the password from")
    parser.add_argument("--replace", action="store_true", help="Rotate an existing account with the same username")
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    try:
        settings = get_settings()
        validate_production_settings(settings)
        result = create_admin_user(
            username=args.username.strip(),
            password=resolve_password(args),
            real_name=args.real_name.strip(),
            department=args.department.strip(),
            replace=args.replace,
        )
    except Exception as exc:
        print(json.dumps({"ok": False, "error": str(exc)}, ensure_ascii=False), file=sys.stderr)
        return 1

    print(json.dumps({"ok": True, "database": str(get_settings().db_path), "user": result}, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
