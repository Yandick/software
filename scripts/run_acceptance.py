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
    parser.add_argument("--allow-llm-not-ready", action="store_true", help="Do not fail when /api/llm/status is not ready")
    args = parser.parse_args()

    checks: list[dict[str, Any]] = []
    base_url = args.base_url.rstrip("/")

    health = request(base_url, "GET", "/api/health")
    checks.append(check(health.get("status") == "ok", "backend health", health))

    ready = request(base_url, "GET", "/api/ready")
    checks.append(check(ready.get("status") == "ok", "backend readiness", ready))

    admin_token = login(base_url, "admin", "admin123")
    auditor_token = login(base_url, "auditor", "audit123")

    llm_status = request(base_url, "GET", "/api/llm/status", token=admin_token)
    llm_ready = llm_status.get("ready") is True
    ready_with_llm = request(base_url, "GET", f"/api/ready?include_llm=true&require_llm={'false' if args.allow_llm_not_ready else 'true'}")
    if args.allow_llm_not_ready:
        checks.append({"status": "warn" if not llm_ready else "pass", "label": "vLLM ready", "detail": llm_status})
        checks.append(
            {
                "status": "warn" if ready_with_llm.get("status") == "degraded" else "pass",
                "label": "readiness with vLLM",
                "detail": ready_with_llm,
            }
        )
    else:
        checks.append(check(llm_ready, "vLLM ready", llm_status))
        checks.append(check(ready_with_llm.get("status") == "ok", "readiness with vLLM", ready_with_llm))

    rag = request(base_url, "GET", "/api/rag/evaluate", token=admin_token)
    checks.append(check(rag.get("pass_rate") == 1.0, "RAG smoke tests", {"pass_rate": rag.get("pass_rate")}))

    if llm_ready:
        qa = request(
            base_url,
            "POST",
            "/api/qa/ask",
            token=admin_token,
            payload={"question": "VPN 提示证书过期，远程办公无法连接，电话 13800138000"},
        )
        checks.append(
            check(
                qa.get("model_status") == "vllm" and qa.get("llm_used") is True,
                "real vLLM chat completion",
                {"conversation_id": qa.get("conversation_id"), "model_status": qa.get("model_status")},
            )
        )

        draft = request(
            base_url,
            "POST",
            "/api/issues/draft",
            token=admin_token,
            payload={"description": "VPN 提示证书过期，影响远程办公，电话 13800138000"},
        )
        checks.append(
            check(
                draft.get("extraction_source") == "llm" and draft.get("llm_status") == "vllm",
                "real vLLM issue draft extraction",
                {"extraction_source": draft.get("extraction_source"), "llm_status": draft.get("llm_status")},
            )
        )
    elif args.allow_llm_not_ready:
        checks.append({"status": "warn", "label": "real vLLM chat/draft checks", "detail": "skipped because vLLM is not ready"})

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
