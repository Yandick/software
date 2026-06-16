from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path

ROOT_DIR = Path(__file__).resolve().parents[1]


def run_package_check(*args: str, env_overrides: dict[str, str] | None = None) -> subprocess.CompletedProcess[str]:
    env = os.environ.copy()
    if env_overrides:
        env.update(env_overrides)
    return subprocess.run(
        [sys.executable, "scripts/package_check.py", *args],
        cwd=ROOT_DIR,
        env=env,
        capture_output=True,
        text=True,
        timeout=120,
    )


def test_package_check_json_passes_for_default_demo_config() -> None:
    result = run_package_check("--json")

    assert result.returncode == 0, result.stderr
    payload = json.loads(result.stdout)
    labels = {item["label"]: item for item in payload["checks"]}
    assert payload["ok"] is True
    assert labels["runtime config"]["status"] == "pass"
    assert labels["frontend/package.json"]["status"] == "pass"
    assert labels["scripts/run_acceptance.py"]["status"] == "pass"


def test_package_check_production_rejects_unsafe_config() -> None:
    result = run_package_check(
        "--production",
        "--json",
        env_overrides={
            "OPS_ENVIRONMENT": "production",
            "OPS_JWT_SECRET": "short",
            "OPS_SEED_DEMO_ACCOUNTS": "true",
        },
    )

    assert result.returncode == 1
    payload = json.loads(result.stdout)
    runtime = next(item for item in payload["checks"] if item["label"] == "runtime config")
    assert payload["ok"] is False
    assert runtime["status"] == "fail"
    assert "OPS_JWT_SECRET" in runtime["detail"]
