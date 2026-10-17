"""Settings from environment variables with safe defaults. No secrets here."""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path

PACKAGE_ROOT = Path(__file__).resolve().parents[2]


@dataclass(frozen=True)
class Settings:
    data_dir: Path = field(default_factory=lambda: Path(os.environ.get("SCREENING_DATA_DIR", PACKAGE_ROOT / "data")))
    output_dir: Path = field(default_factory=lambda: Path(os.environ.get("SCREENING_OUTPUT_DIR", PACKAGE_ROOT / "output")))
    user_agent: str = os.environ.get(
        "SCREENING_USER_AGENT",
        "KlarisScreening/0.1 (Steuerberater-Prototyp; Hackathon; respects robots.txt)",
    )
    # Our own lower bound between two requests to the same host, in seconds.
    # The robots.txt crawl-delay wins when it is larger (BMF: 180 s).
    min_delay_seconds: float = float(os.environ.get("SCREENING_MIN_DELAY", "5"))
    timeout_seconds: float = float(os.environ.get("SCREENING_TIMEOUT", "30"))

    @property
    def db_path(self) -> Path:
        return self.data_dir / "screening.sqlite3"

    @property
    def cache_dir(self) -> Path:
        return self.data_dir / "http-cache"

    @property
    def snapshot_dir(self) -> Path:
        return self.data_dir / "snapshots"
