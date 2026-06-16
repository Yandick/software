from __future__ import annotations

from pathlib import Path

import pytest


def test_redact_published_knowledge_script_redacts_existing_published_rows(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    request: pytest.FixtureRequest,
) -> None:
    db_path = tmp_path / "redact.db"
    monkeypatch.setenv("OPS_DATABASE_URL", f"sqlite:///{db_path}")

    from backend.app.config import get_settings
    from backend.app.database import connect, init_db, utc_now

    get_settings.cache_clear()
    request.addfinalizer(get_settings.cache_clear)
    init_db()
    now = utc_now()
    with connect() as conn:
        cur = conn.execute(
            """
            insert into knowledge(title,content,tags,source_type,status,version,created_at,updated_at)
            values(?,?,?,?,?,?,?,?)
            """,
            (
                "历史敏感 VPN 案例",
                "用户联系电话 13800138000，临时 password=Secret123 后完成处理。",
                "VPN,13800138000",
                "case",
                "published",
                1,
                now,
                now,
            ),
        )
        item_id = int(cur.lastrowid)

    from scripts.redact_published_knowledge import redact_published_knowledge

    dry_run = redact_published_knowledge(dry_run=True)
    assert dry_run["matched"] == 1
    assert dry_run["updated"] == 0
    with connect() as conn:
        before = conn.execute("select content from knowledge where id=?", (item_id,)).fetchone()
    assert "13800138000" in before["content"]

    result = redact_published_knowledge()
    assert result["matched"] == 1
    assert result["updated"] == 1
    with connect() as conn:
        row = conn.execute(
            "select content,tags,version,review_note from knowledge where id=?",
            (item_id,),
        ).fetchone()
        audit_count = conn.execute(
            "select count(*) from audit_logs where event_type='knowledge_redact' and target_id=?",
            (item_id,),
        ).fetchone()[0]

    assert "13800138000" not in row["content"]
    assert "13800138000" not in row["tags"]
    assert "[手机号已脱敏]" in row["content"]
    assert "[敏感凭据已脱敏]" in row["content"]
    assert row["version"] == 2
    assert "历史敏感信息自动脱敏" in row["review_note"]
    assert audit_count == 1
