import asyncio
import sys
from pathlib import Path

import pytest

from app.config import PROFILE_SCHEMA, Settings
from app.profile.schema import ProfileSchema, load_profile_schema

SMALL_SCHEMA = Path(__file__).parent / "fixtures" / "schema_small.json"


def pytest_asyncio_loop_factories(config, item):
    # psycopg's async mode cannot run on the Windows proactor loop.
    if sys.platform == "win32":
        return {"selector": asyncio.SelectorEventLoop}
    return {"default": asyncio.new_event_loop}


@pytest.fixture
def settings(tmp_path):
    """Test settings: no API key, no .env, fast backoff, low render DPI, memory DB."""
    return Settings(
        _env_file=None,
        openai_api_key=None,
        ocr_dpi=50,
        ocr_backoff_min_s=0.01,
        ocr_backoff_max_s=0.02,
        agent_backoff_min_s=0.01,
        agent_backoff_max_s=0.02,
        repository="memory",
        database_url=None,
        profile_schema_path=SMALL_SCHEMA,
        data_dir=tmp_path / "data",
    )


@pytest.fixture
def schema() -> ProfileSchema:
    """Small schema (four scalars and one list) that keeps agent tests readable."""
    return load_profile_schema(SMALL_SCHEMA)


@pytest.fixture
def profil_schema() -> ProfileSchema:
    """The real schema mirroring the teammate's DB (schema `profil`)."""
    return load_profile_schema(PROFILE_SCHEMA)
