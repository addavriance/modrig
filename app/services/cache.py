from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path

from app.db import get_db


async def register_cache_entry(kind: str, key: str, path: Path, sha1: str | None = None, size: int | None = None) -> None:
    db = get_db()
    if size is None and path.exists():
        size = path.stat().st_size

    await db.execute(
        "INSERT OR REPLACE INTO cache_entries(kind, key, path, sha1, size, created_at) VALUES (?, ?, ?, ?, ?, ?)",
        (kind, key, str(path), sha1, size, datetime.now(timezone.utc).isoformat()),
    )
    await db.commit()


async def list_cache_entries(kinds: list[str]) -> list[dict]:
    db = get_db()
    placeholders = ",".join("?" for _ in kinds)

    cur = await db.execute(
        f"SELECT kind, key, path, sha1, size, created_at FROM cache_entries WHERE kind IN ({placeholders}) ORDER BY created_at DESC",
        kinds,
    )
    rows = await cur.fetchall()
    return [
        {"kind": r[0], "key": r[1], "path": r[2], "sha1": r[3], "size": r[4], "created_at": r[5]}
        for r in rows
    ]
