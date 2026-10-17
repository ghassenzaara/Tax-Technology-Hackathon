"""SQLite storage for the screening loop. The only module that knows the database.

Moves to Postgres later behind the same functions. Tables:
- source_item: one row per item key (official id or canonical URL)
- law_version: every distinct content version of an item, with text and hash
- run: one row per run, with counts and errors per source
"""

from __future__ import annotations

import json
import sqlite3
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path

from screening.sources.base import SourceItem

SCHEMA = """
CREATE TABLE IF NOT EXISTS source_item (
  key           TEXT PRIMARY KEY,
  source        TEXT NOT NULL,
  title         TEXT NOT NULL,
  url           TEXT NOT NULL,
  source_type   TEXT NOT NULL,
  source_name   TEXT NOT NULL,
  published_at  TEXT,
  extra         TEXT NOT NULL DEFAULT '{}',
  first_seen    TEXT NOT NULL,
  last_seen     TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS law_version (
  id            INTEGER PRIMARY KEY AUTOINCREMENT,
  item_key      TEXT NOT NULL REFERENCES source_item(key),
  version       INTEGER NOT NULL,
  content_hash  TEXT NOT NULL,
  text          TEXT NOT NULL,
  snapshot_path TEXT,
  fetched_at    TEXT NOT NULL,
  run_id        TEXT NOT NULL,
  UNIQUE (item_key, version)
);
CREATE TABLE IF NOT EXISTS run (
  id           TEXT PRIMARY KEY,
  started_at   TEXT NOT NULL,
  finished_at  TEXT,
  status       TEXT NOT NULL,
  stats        TEXT NOT NULL DEFAULT '{}',
  errors       TEXT NOT NULL DEFAULT '[]'
);
"""


def now_iso() -> str:
    return datetime.now(UTC).isoformat(timespec="seconds")


@dataclass(frozen=True)
class StoredVersion:
    item_key: str
    version: int
    content_hash: str
    text: str
    fetched_at: str
    run_id: str


class Store:
    def __init__(self, path: Path | str) -> None:
        if str(path) != ":memory:":
            Path(path).parent.mkdir(parents=True, exist_ok=True)
        self.db = sqlite3.connect(str(path))
        self.db.row_factory = sqlite3.Row
        self.db.execute("PRAGMA foreign_keys = ON")
        self.db.executescript(SCHEMA)

    def close(self) -> None:
        self.db.close()

    # ---------- items ----------

    def upsert_item(self, item: SourceItem, seen_at: str) -> None:
        self.db.execute(
            """
            INSERT INTO source_item (key, source, title, url, source_type, source_name, published_at, extra, first_seen, last_seen)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(key) DO UPDATE SET
              title = excluded.title, url = excluded.url, published_at = excluded.published_at,
              extra = excluded.extra, last_seen = excluded.last_seen
            """,
            (
                item.key,
                item.source,
                item.title,
                item.url,
                item.source_type,
                item.source_name,
                item.published_at.isoformat() if item.published_at else None,
                json.dumps(item.extra, ensure_ascii=False),
                seen_at,
                seen_at,
            ),
        )
        self.db.commit()

    def items(self) -> list[sqlite3.Row]:
        return self.db.execute("SELECT * FROM source_item ORDER BY published_at DESC, title").fetchall()

    # ---------- versions ----------

    def latest_version(self, item_key: str) -> StoredVersion | None:
        row = self.db.execute(
            "SELECT * FROM law_version WHERE item_key = ? ORDER BY version DESC LIMIT 1", (item_key,)
        ).fetchone()
        return _version(row) if row else None

    def add_version(self, item_key: str, content_hash: str, text: str, snapshot_path: str | None, run_id: str) -> StoredVersion:
        latest = self.latest_version(item_key)
        version = (latest.version if latest else 0) + 1
        fetched_at = now_iso()
        self.db.execute(
            "INSERT INTO law_version (item_key, version, content_hash, text, snapshot_path, fetched_at, run_id) VALUES (?, ?, ?, ?, ?, ?, ?)",
            (item_key, version, content_hash, text, snapshot_path, fetched_at, run_id),
        )
        self.db.commit()
        return StoredVersion(item_key, version, content_hash, text, fetched_at, run_id)

    def versions(self, item_key: str) -> list[StoredVersion]:
        rows = self.db.execute("SELECT * FROM law_version WHERE item_key = ? ORDER BY version", (item_key,)).fetchall()
        return [_version(r) for r in rows]

    # ---------- runs ----------

    def start_run(self, run_id: str) -> None:
        self.db.execute("INSERT INTO run (id, started_at, status) VALUES (?, ?, 'running')", (run_id, now_iso()))
        self.db.commit()

    def finish_run(self, run_id: str, status: str, stats: dict, errors: list[dict]) -> None:
        self.db.execute(
            "UPDATE run SET finished_at = ?, status = ?, stats = ?, errors = ? WHERE id = ?",
            (now_iso(), status, json.dumps(stats, ensure_ascii=False), json.dumps(errors, ensure_ascii=False), run_id),
        )
        self.db.commit()

    def runs(self, limit: int = 10) -> list[sqlite3.Row]:
        return self.db.execute("SELECT * FROM run ORDER BY started_at DESC LIMIT ?", (limit,)).fetchall()


def _version(row: sqlite3.Row) -> StoredVersion:
    return StoredVersion(row["item_key"], row["version"], row["content_hash"], row["text"], row["fetched_at"], row["run_id"])
