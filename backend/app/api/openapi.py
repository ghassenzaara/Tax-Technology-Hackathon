"""Write the OpenAPI contract for the UI team: `uv run python -m app.api.openapi [FILE]`
(default `openapi.json` in the backend folder). No server or database needed."""

import json
import sys
from pathlib import Path

from app.api.app import create_app
from app.config import Settings

DEFAULT = Path(__file__).parent.parent.parent / "openapi.json"


def build_spec() -> dict:
    """The contract as the app generates it (a test compares it with the checked-in file)."""
    return create_app(Settings(_env_file=None)).openapi()


def export(path: Path = DEFAULT) -> Path:
    path.write_text(json.dumps(build_spec(), indent=2, ensure_ascii=False) + "\n", "utf-8")
    return path


if __name__ == "__main__":
    print(export(Path(sys.argv[1]) if len(sys.argv) > 1 else DEFAULT))
