from __future__ import annotations

import aiosqlite

from app.config import settings

_SCHEMA = """
CREATE TABLE IF NOT EXISTS cache_entries (
    kind TEXT NOT NULL,
    key TEXT NOT NULL,
    path TEXT NOT NULL,
    sha1 TEXT,
    size INTEGER,
    created_at TEXT NOT NULL,
    PRIMARY KEY (kind, key)
);

CREATE TABLE IF NOT EXISTS runs (
    id TEXT PRIMARY KEY,
    mc_version TEXT NOT NULL,
    loader TEXT NOT NULL,
    loader_version TEXT,
    mods_json TEXT NOT NULL,
    ephemeral INTEGER NOT NULL,
    status TEXT NOT NULL,
    created_at TEXT NOT NULL,
    finished_at TEXT,
    exit_code INTEGER,
    error TEXT
);
"""

_db: aiosqlite.Connection | None = None


async def init_db() -> aiosqlite.Connection:
    global _db

    settings.ensure_dirs()

    _db = await aiosqlite.connect(settings.db_path)
    await _db.executescript(_SCHEMA)
    await _db.commit()

    return _db


def get_db() -> aiosqlite.Connection:
    if _db is None:
        raise RuntimeError("DB is not initialized yet")
    return _db


async def close_db() -> None:
    global _db
    if _db is not None:
        await _db.close()
        _db = None
