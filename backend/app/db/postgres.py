"""Postgres repository (psycopg 3, async, small pool). Works for Supabase and local Docker.

Our working tables live in schema `pipeline` (app/db/migrations); the profile goes into the
teammate's schema `profil` (backend/db_scheme). Supabase: use the direct or session
connection string. Client-side prepared statements are off, so the transaction-mode pooler
works too. On Windows, psycopg's async mode needs a selector event loop (the CLI and tests
set one up).
"""

import asyncio
import re
from pathlib import Path

import psycopg
from psycopg.rows import dict_row
from psycopg.types.json import Jsonb
from psycopg_pool import AsyncConnectionPool

from app.config import Settings
from app.db.profil_writer import publish_profile
from app.db.repository import PublishReport, Repository
from app.models.extraction import Candidate, DocumentExtraction
from app.models.profile import ProfileDraft
from app.profile.schema import ProfileSchema

MIGRATIONS = Path(__file__).parent / "migrations"
# Order from db_scheme/README.md. The schema file starts with DROP SCHEMA profil CASCADE.
PROFILE_SCHEMA_FILES = ("01_schema.sql", "02_vokabular.sql", "03_scan_und_views.sql")
SEED_PROJECT = re.compile(r"INSERT INTO deal\s*\([^)]*\)\s*VALUES\s*\(\s*'([^']+)'", re.S)

_INSERT_CANDIDATE = """
INSERT INTO pipeline.field_candidates (company_id, field_name, value, evidence, source_doc,
    source_filename, source_page, confidence, needs_review, chunk_index, created_at)
VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
"""

_UPSERT_EXTRACTION = """
INSERT INTO pipeline.document_extractions (company_id, doc_id, filename, status, warnings,
    chunks, repair_used, input_tokens, output_tokens, agent_seconds, document_class,
    document_title, document_date, created_at)
VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
ON CONFLICT (company_id, doc_id) DO UPDATE SET
    filename = EXCLUDED.filename, status = EXCLUDED.status, warnings = EXCLUDED.warnings,
    chunks = EXCLUDED.chunks, repair_used = EXCLUDED.repair_used,
    input_tokens = EXCLUDED.input_tokens, output_tokens = EXCLUDED.output_tokens,
    agent_seconds = EXCLUDED.agent_seconds,
    document_class = COALESCE(EXCLUDED.document_class, document_extractions.document_class),
    document_title = COALESCE(EXCLUDED.document_title, document_extractions.document_title),
    document_date = COALESCE(EXCLUDED.document_date, document_extractions.document_date),
    created_at = EXCLUDED.created_at
"""


class PostgresRepository(Repository):
    def __init__(self, conninfo: str, max_size: int = 5):
        self._conninfo = conninfo
        self._pool = AsyncConnectionPool(
            conninfo,
            min_size=1,
            max_size=max_size,
            open=False,
            kwargs={"prepare_threshold": None},  # no prepared statements (Supabase pooler)
        )
        self._open_lock = asyncio.Lock()
        self._opened = False

    async def _connection(self):
        async with self._open_lock:
            if not self._opened:
                await self._pool.open(wait=True)
                self._opened = True
        return self._pool.connection()

    async def initialize(self) -> None:
        async with await self._connection() as conn:
            for script in sorted(MIGRATIONS.glob("*.sql")):
                await conn.execute(script.read_text("utf-8"))

    async def initialize_profile_schema(
        self, sql_dir: Path, *, reset: bool = False, seed: bool = False
    ) -> list[str]:
        """Apply the teammate's SQL files. Creates `profil` only when it is missing unless
        `reset` (which drops it with all published profiles). Returns the files applied."""
        applied = []
        async with await psycopg.AsyncConnection.connect(
            self._conninfo, autocommit=True, prepare_threshold=None
        ) as conn:
            cur = await conn.execute(
                "SELECT 1 FROM information_schema.schemata WHERE schema_name = 'profil'"
            )
            files = list(PROFILE_SCHEMA_FILES) if reset or not await cur.fetchone() else []
            for name in files:
                await conn.execute((sql_dir / name).read_text("utf-8"))
                applied.append(name)
            if seed:
                for path in sorted(sql_dir.glob("1[0-9]_seed_*.sql")):
                    if await self._seeded(conn, path):
                        continue  # repeatable: a seed is applied once per database
                    await conn.execute(path.read_text("utf-8"))
                    applied.append(path.name)
        return applied

    @staticmethod
    async def _seeded(conn: psycopg.AsyncConnection, path: Path) -> bool:
        """A seed counts as applied when its deal (first projektname it inserts) exists."""
        match = SEED_PROJECT.search(path.read_text("utf-8"))
        if match is None:
            return False
        cur = await conn.execute(
            "SELECT 1 FROM profil.deal WHERE projektname = %s", (match.group(1),)
        )
        return await cur.fetchone() is not None

    async def replace_document_candidates(self, extraction: DocumentExtraction) -> None:
        e = extraction
        async with await self._connection() as conn, conn.transaction():
            if e.status.has_result:  # failed/skipped attempts keep the earlier candidates
                await conn.execute(
                    "DELETE FROM pipeline.field_candidates "
                    "WHERE company_id = %s AND source_doc = %s",
                    (e.company_id, e.doc_id),
                )
            if e.status.has_result and e.candidates:
                async with conn.cursor() as cur:
                    await cur.executemany(
                        _INSERT_CANDIDATE,
                        [
                            (
                                c.company_id,
                                c.field_name,
                                Jsonb(c.value),
                                c.evidence,
                                c.source_doc,
                                c.source_filename,
                                c.source_page,
                                c.confidence,
                                c.needs_review,
                                c.chunk_index,
                                c.created_at,
                            )
                            for c in e.candidates
                        ],
                    )
            await conn.execute(
                _UPSERT_EXTRACTION,
                (
                    e.company_id,
                    e.doc_id,
                    e.filename,
                    str(e.status),
                    Jsonb(e.warnings),
                    e.chunks,
                    e.repair_used,
                    e.token_usage.input_tokens,
                    e.token_usage.output_tokens,
                    e.agent_s,
                    e.document_class,
                    e.document_title,
                    e.document_date,
                    e.created_at,
                ),
            )

    async def list_candidates(self, company_id: str) -> list[Candidate]:
        async with await self._connection() as conn, conn.cursor(row_factory=dict_row) as cur:
            await cur.execute(
                "SELECT company_id, field_name, value, evidence, source_doc, source_filename, "
                "source_page, confidence, needs_review, chunk_index, created_at "
                "FROM pipeline.field_candidates WHERE company_id = %s ORDER BY source_doc, id",
                (company_id,),
            )
            return [Candidate.model_validate(row) for row in await cur.fetchall()]

    async def list_extractions(self, company_id: str) -> list[DocumentExtraction]:
        async with await self._connection() as conn, conn.cursor(row_factory=dict_row) as cur:
            await cur.execute(
                "SELECT company_id, doc_id, filename, status, warnings, chunks, repair_used, "
                "agent_seconds AS agent_s, document_class, document_title, "
                "document_date::text AS document_date, created_at, "
                "json_build_object('input_tokens', input_tokens, "
                "'output_tokens', output_tokens) AS token_usage "
                "FROM pipeline.document_extractions WHERE company_id = %s ORDER BY filename",
                (company_id,),
            )
            return [DocumentExtraction.model_validate(row) for row in await cur.fetchall()]

    async def save_profile_draft(self, draft: ProfileDraft) -> None:
        async with await self._connection() as conn:
            await conn.execute(
                "INSERT INTO pipeline.profile_drafts (company_id, draft, created_at) "
                "VALUES (%s, %s, %s)",
                (draft.company_id, Jsonb(draft.model_dump(mode="json")), draft.created_at),
            )

    async def get_profile_draft(self, company_id: str) -> ProfileDraft | None:
        async with await self._connection() as conn, conn.cursor(row_factory=dict_row) as cur:
            await cur.execute(
                "SELECT draft FROM pipeline.profile_drafts WHERE company_id = %s "
                "ORDER BY created_at DESC, id DESC LIMIT 1",
                (company_id,),
            )
            row = await cur.fetchone()
            return ProfileDraft.model_validate(row["draft"]) if row else None

    async def publish_profile(
        self,
        draft: ProfileDraft,
        extractions: list[DocumentExtraction],
        schema: ProfileSchema,
        settings: Settings,
    ) -> PublishReport:
        async with await self._connection() as conn, conn.transaction():
            return await publish_profile(conn, draft, extractions, schema, settings)

    async def close(self) -> None:
        if self._opened:
            await self._pool.close()
            self._opened = False
