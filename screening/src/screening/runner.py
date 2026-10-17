"""One screening run: list every source, fetch items, detect new and changed versions.

- Item key = official id or canonical URL. Content hash on normalized text.
- New key: version 1, "new". Known key with a new hash: next version, "updated".
  Same hash: "unchanged", nothing stored.
- One failing source or item never stops the others; errors are recorded in the run.
"""

from __future__ import annotations

import hashlib
import logging
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path

from screening.http import DisallowedByRobots, PoliteClient
from screening.normalize import content_hash, normalize_text
from screening.sources.base import Fetcher, SourceItem
from screening.store import Store, now_iso

log = logging.getLogger(__name__)


@dataclass
class ItemResult:
    key: str
    title: str
    change: str  # new | updated | unchanged | error
    version: int | None = None
    error: str | None = None


@dataclass
class RunResult:
    run_id: str
    status: str = "running"
    items: list[ItemResult] = field(default_factory=list)
    errors: list[dict] = field(default_factory=list)

    def count(self, change: str) -> int:
        return sum(1 for i in self.items if i.change == change)

    @property
    def stats(self) -> dict:
        return {c: self.count(c) for c in ("new", "updated", "unchanged", "error")}


def new_run_id() -> str:
    return "run-" + datetime.now(UTC).strftime("%Y-%m-%dT%H%M%SZ")


def process_item(store: Store, item: SourceItem, text: str, raw: bytes, run_id: str, snapshot_dir: Path | None) -> ItemResult:
    """Stores a new version when the normalized content changed. Pure apart from the store."""
    store.upsert_item(item, now_iso())
    digest = content_hash(text)
    latest = store.latest_version(item.key)
    if latest and latest.content_hash == digest:
        return ItemResult(item.key, item.title, "unchanged", latest.version)
    snapshot = _write_snapshot(snapshot_dir, item.key, (latest.version + 1) if latest else 1, raw) if snapshot_dir else None
    version = store.add_version(item.key, digest, normalize_text(text), snapshot, run_id)
    return ItemResult(item.key, item.title, "updated" if latest else "new", version.version)


def _write_snapshot(snapshot_dir: Path, key: str, version: int, raw: bytes) -> str:
    folder = snapshot_dir / hashlib.sha256(key.encode("utf-8")).hexdigest()[:16]
    folder.mkdir(parents=True, exist_ok=True)
    path = folder / f"v{version}.bin"
    path.write_bytes(raw)
    return str(path)


def run(
    fetchers: list[Fetcher],
    store: Store,
    client: PoliteClient,
    *,
    limit: int | None = None,
    snapshot_dir: Path | None = None,
    run_id: str | None = None,
) -> RunResult:
    result = RunResult(run_id or new_run_id())
    store.start_run(result.run_id)
    for fetcher in fetchers:
        try:
            items = fetcher.list_items(client, limit=limit)
            log.info("%s: %d items listed", fetcher.name, len(items))
        except Exception as exc:  # one source must not stop the others
            log.exception("%s: listing failed", fetcher.name)
            result.errors.append({"source": fetcher.name, "stage": "list", "error": _describe(exc)})
            continue
        for item in items:
            try:
                doc = fetcher.fetch(client, item)
                result.items.append(process_item(store, item, doc.text, doc.raw, result.run_id, snapshot_dir))
            except Exception as exc:
                log.warning("%s: %s failed: %s", fetcher.name, item.key, exc)
                result.items.append(ItemResult(item.key, item.title, "error", error=_describe(exc)))
                result.errors.append({"source": fetcher.name, "stage": "fetch", "key": item.key, "error": _describe(exc)})
    listing_failed = len(result.errors) > 0 and not result.items
    result.status = "failed" if listing_failed else ("partial" if result.errors else "ok")
    store.finish_run(result.run_id, result.status, result.stats, result.errors)
    return result


def _describe(exc: Exception) -> str:
    if isinstance(exc, DisallowedByRobots):
        return f"robots.txt verbietet den Abruf: {exc}"
    return f"{type(exc).__name__}: {exc}"
