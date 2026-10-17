"""Polite HTTP access to public sources.

- Honors robots.txt: disallowed paths are never requested, crawl-delay is respected.
- One request per host at a time, at least max(crawl-delay, min_delay) apart. The last
  request time is stored on disk, so separate runs also keep the distance.
- Disk cache with conditional requests (ETag / Last-Modified) and a short TTL, so
  repeated runs do not download unchanged pages again.
"""

from __future__ import annotations

import hashlib
import json
import time
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from urllib.parse import urlsplit
from urllib.robotparser import RobotFileParser

import httpx


class DisallowedByRobots(Exception):
    """The URL is disallowed for our user agent by the site's robots.txt."""


@dataclass(frozen=True)
class Response:
    url: str
    status: int
    content: bytes
    content_type: str
    from_cache: bool

    @property
    def text(self) -> str:
        return self.content.decode("utf-8", errors="replace")


class PoliteClient:
    def __init__(
        self,
        *,
        user_agent: str,
        cache_dir: Path,
        min_delay_seconds: float = 5.0,
        timeout_seconds: float = 30.0,
        cache_ttl_seconds: float = 12 * 3600,
        transport: httpx.BaseTransport | None = None,
        clock: Callable[[], float] = time.time,
        sleep: Callable[[float], None] = time.sleep,
    ) -> None:
        self.user_agent = user_agent
        self.cache_dir = cache_dir
        self.min_delay = min_delay_seconds
        self.cache_ttl = cache_ttl_seconds
        self._clock = clock
        self._sleep = sleep
        self._robots: dict[str, RobotFileParser] = {}
        self._client = httpx.Client(
            headers={"User-Agent": user_agent, "Accept-Language": "de-DE,de;q=0.9"},
            timeout=timeout_seconds,
            follow_redirects=True,
            transport=transport,
        )
        self.cache_dir.mkdir(parents=True, exist_ok=True)
        self.requests_made = 0

    # ---------- public ----------

    def get(self, url: str) -> Response:
        host = self._host(url)
        robots = self._robots_for(url)
        if not robots.can_fetch(self.user_agent, url):
            raise DisallowedByRobots(url)

        meta, body = self._read_cache(url)
        if meta and body is not None and self._clock() - meta["fetched_at"] < self.cache_ttl:
            return Response(url, meta["status"], body, meta.get("content_type", ""), from_cache=True)

        headers: dict[str, str] = {}
        if meta and body is not None:
            if meta.get("etag"):
                headers["If-None-Match"] = meta["etag"]
            if meta.get("last_modified"):
                headers["If-Modified-Since"] = meta["last_modified"]

        self._wait_turn(host, self._delay_for(robots))
        r = self._client.get(url, headers=headers)
        self._mark_request(host)

        if r.status_code == 304 and meta and body is not None:
            meta["fetched_at"] = self._clock()
            self._write_cache(url, meta, body)
            return Response(url, meta["status"], body, meta.get("content_type", ""), from_cache=True)

        r.raise_for_status()
        meta = {
            "url": str(r.url),
            "status": r.status_code,
            "content_type": r.headers.get("content-type", ""),
            "etag": r.headers.get("etag"),
            "last_modified": r.headers.get("last-modified"),
            "fetched_at": self._clock(),
        }
        self._write_cache(url, meta, r.content)
        return Response(str(r.url), r.status_code, r.content, meta["content_type"], from_cache=False)

    def close(self) -> None:
        self._client.close()

    # ---------- robots ----------

    def _robots_for(self, url: str) -> RobotFileParser:
        host = self._host(url)
        if host in self._robots:
            return self._robots[host]
        parts = urlsplit(url)
        robots_url = f"{parts.scheme}://{parts.netloc}/robots.txt"
        parser = RobotFileParser(robots_url)
        meta, body = self._read_cache(robots_url)
        if not (meta and body is not None and self._clock() - meta["fetched_at"] < 24 * 3600):
            self._wait_turn(host, self.min_delay)
            r = self._client.get(robots_url)
            self._mark_request(host)
            if r.status_code >= 400:
                # No robots.txt: everything allowed, our own delay applies.
                body = b""
            else:
                body = r.content
            self._write_cache(robots_url, {"status": r.status_code, "fetched_at": self._clock()}, body)
        parser.parse(body.decode("utf-8", errors="replace").splitlines())
        self._robots[host] = parser
        return parser

    def _delay_for(self, robots: RobotFileParser) -> float:
        crawl_delay = robots.crawl_delay(self.user_agent)
        return max(self.min_delay, float(crawl_delay or 0))

    # ---------- spacing between requests ----------

    def _state_file(self) -> Path:
        return self.cache_dir / "last-request.json"

    def _last_requests(self) -> dict[str, float]:
        try:
            return json.loads(self._state_file().read_text(encoding="utf-8"))
        except (FileNotFoundError, json.JSONDecodeError):
            return {}

    def _wait_turn(self, host: str, delay: float) -> None:
        last = self._last_requests().get(host)
        if last is not None:
            wait = last + delay - self._clock()
            if wait > 0:
                self._sleep(wait)

    def _mark_request(self, host: str) -> None:
        state = self._last_requests()
        state[host] = self._clock()
        self._state_file().write_text(json.dumps(state), encoding="utf-8")
        self.requests_made += 1

    # ---------- cache ----------

    @staticmethod
    def _host(url: str) -> str:
        return urlsplit(url).netloc.lower()

    def _cache_paths(self, url: str) -> tuple[Path, Path]:
        digest = hashlib.sha256(url.encode("utf-8")).hexdigest()[:32]
        return self.cache_dir / f"{digest}.json", self.cache_dir / f"{digest}.body"

    def _read_cache(self, url: str) -> tuple[dict | None, bytes | None]:
        meta_path, body_path = self._cache_paths(url)
        try:
            return json.loads(meta_path.read_text(encoding="utf-8")), body_path.read_bytes()
        except (FileNotFoundError, json.JSONDecodeError):
            return None, None

    def _write_cache(self, url: str, meta: dict, body: bytes) -> None:
        meta_path, body_path = self._cache_paths(url)
        body_path.write_bytes(body)
        meta_path.write_text(json.dumps(meta), encoding="utf-8")
