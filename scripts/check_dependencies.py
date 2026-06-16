from __future__ import annotations

import importlib
import json
import subprocess
import sys

REQUIRED = {
    "fastapi": "fastapi",
    "python-multipart": "multipart",
    "uvicorn": "uvicorn",
    "pydantic-settings": "pydantic_settings",
    "PyJWT": "jwt",
    "argon2-cffi": "argon2",
    "httpx": "httpx",
    "alembic": "alembic",
    "torch": "torch",
    "transformers": "transformers",
    "vllm": "vllm",
    "qwen-agent": "qwen_agent",
    "soundfile": "soundfile",
}

OPTIONAL = {}


def version(pkg: str) -> str:
    try:
        out = subprocess.check_output([sys.executable, "-m", "pip", "show", pkg], text=True)
        for line in out.splitlines():
            if line.startswith("Version:"):
                return line.split(":", 1)[1].strip()
    except Exception:
        return "unknown"
    return "unknown"


def check(items: dict[str, str]) -> list[dict[str, str]]:
    result = []
    for pkg, module in items.items():
        try:
            importlib.import_module(module)
            result.append({"package": pkg, "module": module, "status": "ok", "version": version(pkg)})
        except Exception as exc:
            result.append({"package": pkg, "module": module, "status": f"missing:{exc.__class__.__name__}", "version": ""})
    return result


if __name__ == "__main__":
    print(json.dumps({"required": check(REQUIRED), "optional": check(OPTIONAL)}, ensure_ascii=False, indent=2))
