#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path
from typing import Any

ROOT_DIR = Path(__file__).resolve().parents[1]
if str(ROOT_DIR) not in sys.path:
    sys.path.insert(0, str(ROOT_DIR))

from backend.app.config import get_settings
from backend.app.database import connect, utc_now, write_audit
from backend.app.services.knowledge_service import scan_knowledge_sensitive


REVIEW_NOTE = "历史敏感信息自动脱敏"


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Redact sensitive values already present in published knowledge rows.")
    parser.add_argument("--database-url", default="", help="Override OPS_DATABASE_URL, for example sqlite:///backend/data/app.db")
    parser.add_argument("--dry-run", action="store_true", help="Only report rows that would be redacted.")
    return parser.parse_args()


def merged_review_note(existing: str) -> str:
    if not existing:
        return REVIEW_NOTE
    if REVIEW_NOTE in existing:
        return existing
    return f"{existing}; {REVIEW_NOTE}"


def redact_published_knowledge(*, dry_run: bool = False) -> dict[str, Any]:
    updated: list[dict[str, Any]] = []
    scanned = 0
    with connect() as conn:
        rows = conn.execute(
            """
            select id,title,content,tags,version,review_note
            from knowledge
            where status='published'
            order by id
            """
        ).fetchall()
        for row in rows:
            scanned += 1
            check = scan_knowledge_sensitive(row["title"], row["content"], row["tags"])
            if not check["has_sensitive"]:
                continue

            redacted = check["redacted"]
            changed = any(redacted[field] != row[field] for field in ("title", "content", "tags"))
            item = {
                "findings": check["findings"],
                "id": row["id"],
                "redacted": changed,
                "title": redacted["title"],
            }
            updated.append(item)
            if dry_run or not changed:
                continue

            now = utc_now()
            next_version = int(row["version"] or 1) + 1
            conn.execute(
                """
                update knowledge
                set title=?,content=?,tags=?,version=?,review_note=?,updated_at=?
                where id=?
                """,
                (
                    redacted["title"],
                    redacted["content"],
                    redacted["tags"],
                    next_version,
                    merged_review_note(row["review_note"] or ""),
                    now,
                    row["id"],
                ),
            )
            write_audit(
                conn,
                "knowledge_redact",
                "knowledge",
                f"历史发布知识自动脱敏：{redacted['title']}",
                int(row["id"]),
            )
    return {
        "dry_run": dry_run,
        "matched": len(updated),
        "scanned": scanned,
        "updated": 0 if dry_run else sum(1 for item in updated if item["redacted"]),
        "rows": updated,
    }


def main() -> int:
    args = parse_args()
    if args.database_url:
        os.environ["OPS_DATABASE_URL"] = args.database_url
        get_settings.cache_clear()
    result = redact_published_knowledge(dry_run=args.dry_run)
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
