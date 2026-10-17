import asyncio
import os
import sys
import uuid
from pathlib import Path

import psycopg
import pytest

from app.config import PROFILE_DB_DIR, PROFILE_SCHEMA, Settings
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
        embedding_dimension=4,  # matches FakeClient's default vectors
    )


@pytest.fixture
def schema() -> ProfileSchema:
    """Small schema (four scalars and one list) that keeps agent tests readable."""
    return load_profile_schema(SMALL_SCHEMA)


@pytest.fixture
def profil_schema() -> ProfileSchema:
    """The real schema mirroring the teammate's DB (schema `profil`)."""
    return load_profile_schema(PROFILE_SCHEMA)


@pytest.fixture
def project() -> str:
    """A fresh project name (company id), so runs on a shared database never collide."""
    return f"test-{uuid.uuid4().hex[:10]}"


@pytest.fixture
async def pg_repository():
    """Postgres with pipeline tables and schema profil (created when missing, never reset,
    so seeds stay intact). Skipped without TEST_DATABASE_URL."""
    url = os.environ.get("TEST_DATABASE_URL")
    if not url:
        pytest.skip("TEST_DATABASE_URL not set")
    from app.db.postgres import PostgresRepository

    repo = PostgresRepository(url)
    await repo.initialize()
    await repo.initialize_profile_schema(PROFILE_DB_DIR)
    async with await psycopg.AsyncConnection.connect(url) as conn:
        cur = await conn.execute("SELECT coalesce(max(id), 0) FROM screening.law")
        (last_law,) = await cur.fetchone()
    yield repo
    await repo.close()
    # Fake sources (2-dim vectors) must not stay behind: screening every stored law
    # would fail on them. Real sources and live projects are never touched.
    async with await psycopg.AsyncConnection.connect(url, autocommit=True) as conn:
        await conn.execute(
            "DELETE FROM screening.outreach WHERE company_id LIKE 'test-%' "
            "OR alert_id IN (SELECT id FROM screening.alert WHERE company_id LIKE 'test-%')"
        )
        await conn.execute("DELETE FROM screening.alert WHERE company_id LIKE 'test-%'")
        await conn.execute(
            "DELETE FROM screening.law WHERE id > %s AND (content_hash LIKE 'test%%' "
            "OR filename LIKE 'test-%%' OR filename = 'gesetz.pdf')",
            (last_law,),
        )
