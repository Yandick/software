#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import sys
import urllib.error
import urllib.request
from typing import Any


def request(base_url: str, method: str, path: str, token: str = "", payload: dict[str, Any] | None = None) -> Any:
    body = None
    headers = {"Accept": "application/json"}
    if payload is not None:
        body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        headers["Content-Type"] = "application/json"
    if token:
        headers["Authorization"] = f"Bearer {token}"
    req = urllib.request.Request(f"{base_url.rstrip('/')}{path}", data=body, headers=headers, method=method)
    try:
        with urllib.request.urlopen(req, timeout=120) as resp:
            text = resp.read().decode("utf-8")
            return json.loads(text) if text else {}
    except urllib.error.HTTPError as exc:
        detail = exc.read().decode("utf-8", errors="replace")
        raise RuntimeError(f"{method} {path} failed: HTTP {exc.code} {detail}") from exc


def login(base_url: str, username: str, password: str) -> str:
    result = request(base_url, "POST", "/api/auth/login", payload={"username": username, "password": password})
    token = result.get("access_token")
    if not token:
        raise RuntimeError(f"login failed for {username}")
    return token


def check(condition: bool, label: str, detail: Any = "") -> dict[str, Any]:
    if not condition:
        raise RuntimeError(f"{label} failed: {detail}")
    return {"status": "pass", "label": label, "detail": detail}


def main() -> int:
    parser = argparse.ArgumentParser(description="Run end-to-end acceptance checks for the ops digital employee project.")
    parser.add_argument("--base-url", default="http://127.0.0.1:8010", help="Backend base URL, without trailing /api")
    parser.add_argument("--skip-demo", action="store_true", help="Skip the 12-step demo workflow")
    parser.add_argument("--allow-llm-not-ready", action="store_true", help="Do not fail when /api/llm/status is not ready")
    args = parser.parse_args()

    checks: list[dict[str, Any]] = []
    base_url = args.base_url.rstrip("/")

    health = request(base_url, "GET", "/api/health")
    checks.append(check(health.get("status") == "ok", "backend health", health))

    admin_token = login(base_url, "admin", "admin123")
    auditor_token = login(base_url, "auditor", "audit123")

    llm_status = request(base_url, "GET", "/api/llm/status", token=admin_token)
    if args.allow_llm_not_ready:
        checks.append({"status": "warn" if not llm_status.get("ready") else "pass", "label": "vLLM ready", "detail": llm_status})
    else:
        checks.append(check(llm_status.get("ready") is True, "vLLM ready", llm_status))

    rag = request(base_url, "GET", "/api/rag/evaluate", token=admin_token)
    checks.append(check(rag.get("pass_rate") == 1.0, "RAG smoke tests", {"pass_rate": rag.get("pass_rate")}))

    sensitive = request(
        base_url,
        "POST",
        "/api/knowledge/sensitive-check",
        token=admin_token,
        payload={"title": "VPN 案例", "content": "用户电话 13800138000，password=abc123", "tags": "VPN,案例"},
    )
    checks.append(
        check(
            sensitive.get("blocking") is True and "[手机号已脱敏]" in sensitive.get("redacted", {}).get("content", ""),
            "knowledge sensitive check",
            {"findings": sensitive.get("findings")},
        )
    )

    if not args.skip_demo:
        demo = request(base_url, "POST", "/api/demo/session", token=admin_token)
        for _ in range(len(demo.get("steps", []))):
            demo = request(base_url, "POST", f"/api/demo/session/{demo['id']}/step", token=admin_token)
        checks.append(
            check(
                demo.get("status") == "finished"
                and demo.get("ops_window", {}).get("issue", {}).get("status") == "closed"
                and demo.get("admin_window", {}).get("knowledge", {}).get("status") == "published",
                "12-step closed-loop demo",
                {"demo_id": demo.get("id"), "status": demo.get("status")},
            )
        )

    audit_export = request(base_url, "GET", "/api/audit/export?limit=50", token=auditor_token)
    checks.append(
        check(
            "log_type,id,event_type" in audit_export.get("content", ""),
            "audit CSV export",
            {"count": audit_export.get("count"), "filename": audit_export.get("filename")},
        )
    )

    print(json.dumps({"ok": True, "base_url": base_url, "checks": checks}, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except Exception as exc:
        print(json.dumps({"ok": False, "error": str(exc)}, ensure_ascii=False, indent=2), file=sys.stderr)
        raise SystemExit(1)
