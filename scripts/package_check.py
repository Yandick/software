#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
from pathlib import Path
from typing import Any

ROOT_DIR = Path(__file__).resolve().parents[1]
REQUIRED_FILES = [
    ".env.example",
    "README.md",
    "requirements-minimal.txt",
    "alembic.ini",
    "backend/app/main.py",
    "backend/migrations/versions/20260522_0001_baseline.py",
    "frontend/package.json",
    "frontend/pnpm-lock.yaml",
    "scripts/start_all.sh",
    "scripts/start_deploy.sh",
    "scripts/stop_all.sh",
    "scripts/run_acceptance.py",
]


def run(command: list[str], timeout: int = 120) -> subprocess.CompletedProcess[str]:
    return subprocess.run(command, cwd=ROOT_DIR, capture_output=True, text=True, timeout=timeout)


def check(status: str, label: str, detail: Any = "") -> dict[str, Any]:
    return {"status": status, "label": label, "detail": detail}


def check_required_files() -> list[dict[str, Any]]:
    result = []
    for relative in REQUIRED_FILES:
        path = ROOT_DIR / relative
        result.append(check("pass" if path.exists() else "fail", relative, "present" if path.exists() else "missing"))
    return result


def check_runtime_config(production: bool) -> dict[str, Any]:
    sys.path.insert(0, str(ROOT_DIR))
    from backend.app.config import get_settings, validate_production_settings

    get_settings.cache_clear()
    settings = get_settings()
    if production and not settings.is_production:
        return check("fail", "production config", "set OPS_ENVIRONMENT=production for production package checks")
    try:
        validate_production_settings(settings)
    except RuntimeError as exc:
        return check("fail", "runtime config", str(exc))
    return check(
        "pass",
        "runtime config",
        {
            "environment": settings.environment_name,
            "database_url": settings.database_url,
            "seed_demo_accounts": settings.seed_demo_accounts,
        },
    )


def check_model_path() -> dict[str, Any]:
    sys.path.insert(0, str(ROOT_DIR))
    from backend.app.config import get_settings

    settings = get_settings()
    path = Path(settings.model_path)
    if not path.is_absolute():
        path = ROOT_DIR / path
    status = "pass" if path.exists() else "warn"
    return check(status, "model path", str(path))


def check_embedding_model_path() -> dict[str, Any]:
    sys.path.insert(0, str(ROOT_DIR))
    from backend.app.config import get_settings

    settings = get_settings()
    path = Path(settings.embedding_model_path)
    if not path.is_absolute():
        path = ROOT_DIR / path
    status = "pass" if path.exists() else "warn"
    return check(status, "embedding model path", str(path))


def check_embedding_index_config() -> dict[str, Any]:
    sys.path.insert(0, str(ROOT_DIR))
    from backend.app.config import get_settings

    settings = get_settings()
    path = Path(settings.embedding_index_dir)
    if not path.is_absolute():
        path = ROOT_DIR / path
    try:
        import faiss  # noqa: F401

        faiss_status = "available"
        status = "pass"
    except Exception:
        faiss_status = "not installed; numpy index will be used"
        status = "warn"
    return check(
        status,
        "embedding index",
        {
            "backend": settings.embedding_index_backend,
            "dir": str(path),
            "faiss": faiss_status,
        },
    )


def check_frontend_manifest() -> dict[str, Any]:
    package_file = ROOT_DIR / "frontend" / "package.json"
    data = json.loads(package_file.read_text(encoding="utf-8"))
    engines = data.get("engines", {})
    return check(
        "pass",
        "frontend manifest",
        {"name": data.get("name"), "version": data.get("version"), "node": engines.get("node"), "pnpm": engines.get("pnpm")},
    )


def check_command_version(command: str, args: list[str]) -> dict[str, Any]:
    candidates = [command]
    env_command = Path(sys.executable).resolve().parent / command
    if env_command.exists():
        candidates.insert(0, str(env_command))
    last_error = ""
    for candidate in candidates:
        try:
            result = run([candidate, *args], timeout=20)
        except FileNotFoundError as exc:
            last_error = exc.__class__.__name__
            continue
        if result.returncode != 0:
            return check("warn", command, result.stderr.strip() or result.stdout.strip())
        return check("pass", command, result.stdout.strip())
    return check("warn", command, last_error or "not found")


def main() -> int:
    parser = argparse.ArgumentParser(description="Offline package readiness checks for the ops digital employee project.")
    parser.add_argument("--production", action="store_true", help="Fail unless the current OPS_* config is production-safe")
    parser.add_argument("--json", action="store_true", help="Print machine-readable JSON only")
    args = parser.parse_args()

    os.chdir(ROOT_DIR)
    checks: list[dict[str, Any]] = []
    checks.extend(check_required_files())
    checks.append(check_runtime_config(args.production))
    checks.append(check_model_path())
    checks.append(check_embedding_model_path())
    checks.append(check_embedding_index_config())
    checks.append(check_frontend_manifest())
    checks.append(check_command_version(sys.executable, ["--version"]))
    checks.append(check_command_version("node", ["--version"]))
    checks.append(check_command_version("pnpm", ["--version"]))

    ok = not any(item["status"] == "fail" for item in checks)
    payload = {"ok": ok, "checks": checks}
    if args.json:
        print(json.dumps(payload, ensure_ascii=False, indent=2))
    else:
        for item in checks:
            print(f"[{item['status']}] {item['label']}: {item['detail']}")
        print(json.dumps({"ok": ok}, ensure_ascii=False))
    return 0 if ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
