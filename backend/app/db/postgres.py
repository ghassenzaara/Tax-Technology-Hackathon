"""Postgres repository (psycopg 3, async, small pool). Works for Supabase and local Docker.

Our working tables live in schema `pipeline` (app/db/migrations); the profile goes into the
teammate's schema `profil` (backend/db_scheme). Supabase: use the direct or session
connection string. Client-side prepared statements are off, so the transaction-mode pooler
works too. On Windows, psycopg's async mode needs a selector event loop (the CLI and tests
set one up).
"""

import asyncio
import re
from datetime import date
from pathlib import Path

import psycopg
from psycopg.rows import dict_row
from psycopg.types.json import Jsonb
from psycopg_pool import AsyncConnectionPool

from app.config import Settings
from app.db.audit import read_audit
from app.db.profil_reader import load_snapshot, target_company
from app.db.profil_updater import read_profile, update_profile
from app.db.profil_writer import publish_profile
from app.db.repository import (
    WORKING_TABLES,
    BusyError,
    PublishReport,
    Repository,
    RepositoryError,
    StaleDraftError,
    StateError,
)
from app.models.audit import AuditEntry, AuditFilter
from app.models.extraction import Candidate, DocumentExtraction
from app.models.features import EmbeddingRecord, ProfileSnapshot
from app.models.profile import ProfileDraft
from app.models.project import EMBEDDABLE, Confirmation, ProjectState, ProjectStatus
from app.models.review import HintRow
from app.models.run import Run, RunDocument
from app.models.update import ConfirmedProfile, UpdateRequest, UpdateResult
from app.profile.review import draft_version
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

_UPSERT_RUN = """
INSERT INTO pipeline.runs (run_id, company_id, project_name, state, folder, started_by,
    created_at, started_at, finished_at, counts, published, last_error)
VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
ON CONFLICT (run_id) DO UPDATE SET
    state = EXCLUDED.state, folder = EXCLUDED.folder, started_at = EXCLUDED.started_at,
    finished_at = EXCLUDED.finished_at, counts = EXCLUDED.counts,
    published = EXCLUDED.published, last_error = EXCLUDED.last_error
"""

_UPSERT_RUN_DOCUMENT = """
INSERT INTO pipeline.run_documents (run_id, company_id, relative_path, doc_id, parse_status,
    extraction_status, warnings, pages, seconds, input_tokens, output_tokens)
VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
ON CONFLICT (run_id, relative_path) DO UPDATE SET
    doc_id = EXCLUDED.doc_id, parse_status = EXCLUDED.parse_status,
    extraction_status = EXCLUDED.extraction_status, warnings = EXCLUDED.warnings,
    pages = EXCLUDED.pages, seconds = EXCLUDED.seconds,
    input_tokens = EXCLUDED.input_tokens, output_tokens = EXCLUDED.output_tokens,
    updated_at = now()
"""

# Teammate's table; the vector stays real[] for now.
# TODO: switch to vector(1536) with an HNSW index when matching is built (schema owner).
_INSERT_EMBEDDING = """
INSERT INTO profil.embedding (objekt_typ, objekt_id, gesellschaft_id, steuerarten, normen,
    jahre, offen, modell, vektor, text_basis)
VALUES (%s, %s, %s, %s::profil.steuerart[], %s::text[], %s::int4range, %s, %s, %s::real[], %s)
"""


def _range(jahre: tuple[int | None, int | None] | None) -> str | None:
    """Inclusive year range as int4range literal; an open end stays open."""
    if jahre is None:
        return None
    von, bis = jahre
    return f"[{'' if von is None else von},{'' if bis is None else bis}]"


async def _target_company(conn: psycopg.AsyncConnection, company_id: str) -> int:
    gesellschaft = await target_company(conn, company_id)
    if gesellschaft is None:
        raise RepositoryError(f"project '{company_id}' has no target company in profil")
    return gesellschaft


async def _embedding_keys(conn: psycopg.AsyncConnection, gesellschaft: int, model: str) -> set[str]:
    cur = await conn.execute(
        "SELECT objekt_typ || ':' || objekt_id FROM profil.embedding "
        "WHERE gesellschaft_id = %s AND modell = %s",
        (gesellschaft, model),
    )
    return {row[0] for row in await cur.fetchall()}


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

    # Steps 8 to 10 -------------------------------------------------------------------

    async def get_project_state(self, company_id: str) -> ProjectStatus | None:
        async with await self._connection() as conn, conn.cursor(row_factory=dict_row) as cur:
            await cur.execute(
                "SELECT * FROM pipeline.project_state WHERE company_id = %s", (company_id,)
            )
            row = await cur.fetchone()
            return ProjectStatus.model_validate(row) if row else None

    async def record_error(self, company_id: str, error: str) -> None:
        async with await self._connection() as conn:
            await conn.execute(
                "UPDATE pipeline.project_state SET last_error = %s, updated_at = now() "
                "WHERE company_id = %s",
                (error[:2000], company_id),
            )

    async def list_open_hints(self, company_id: str) -> list[HintRow]:
        async with await self._connection() as conn, conn.cursor(row_factory=dict_row) as cur:
            await cur.execute(
                "SELECT p.id, p.art, p.feld, p.beschreibung FROM profil.pruefhinweis p "
                "JOIN profil.gesellschaft g ON g.id = p.gesellschaft_id "
                "JOIN profil.deal d ON d.id = g.deal_id "
                "WHERE d.projektname = %s AND p.status = 'offen' ORDER BY p.id",
                (company_id,),
            )
            return [HintRow.model_validate(row) for row in await cur.fetchall()]

    async def confirm_profile(
        self,
        draft: ProfileDraft,
        extractions: list[DocumentExtraction],
        schema: ProfileSchema,
        settings: Settings,
        confirmation: Confirmation,
    ) -> PublishReport:
        async with await self._connection() as conn, conn.transaction():
            # Lock the state row first, so two confirms of one project run one after another.
            cur = await conn.execute(
                "SELECT state FROM pipeline.project_state WHERE company_id = %s FOR UPDATE",
                (draft.company_id,),
            )
            row = await cur.fetchone()
            if row is None or row[0] != ProjectState.PUBLISHED:
                state = row[0] if row else "not published"
                raise StateError(
                    f"project '{draft.company_id}' is {state}: confirm needs state published"
                )
            cur = await conn.execute(
                "SELECT draft FROM pipeline.profile_drafts WHERE company_id = %s "
                "ORDER BY created_at DESC, id DESC LIMIT 1",
                (draft.company_id,),
            )
            row = await cur.fetchone()
            current = draft_version(ProfileDraft.model_validate(row[0])) if row else None
            if current != confirmation.draft_version:
                raise StaleDraftError(
                    f"draft version {confirmation.draft_version} is stale (current: {current}); "
                    "reload the review"
                )
            return await publish_profile(conn, draft, extractions, schema, settings, confirmation)

    async def load_profile_snapshot(
        self, company_id: str, schema: ProfileSchema
    ) -> ProfileSnapshot:
        async with await self._connection() as conn:
            return await load_snapshot(conn, company_id)

    async def replace_embeddings(
        self,
        company_id: str,
        model: str,
        records: list[EmbeddingRecord],
        fallback: int,
    ) -> None:
        expected = sorted(r.ref for r in records)
        async with await self._connection() as conn, conn.transaction():
            cur = await conn.execute(
                "SELECT state FROM pipeline.project_state WHERE company_id = %s FOR UPDATE",
                (company_id,),
            )
            row = await cur.fetchone()
            state = row[0] if row else None
            if state not in EMBEDDABLE:
                raise StateError(
                    f"project '{company_id}' is {state or 'unknown'}: embedding needs a "
                    "confirmed profile"
                )
            gesellschaft = await _target_company(conn, company_id)
            await conn.execute(
                "DELETE FROM profil.embedding WHERE gesellschaft_id = %s AND modell = %s",
                (gesellschaft, model),
            )
            async with conn.cursor() as cur:
                await cur.executemany(
                    _INSERT_EMBEDDING,
                    [
                        (
                            r.objekt_typ,
                            r.objekt_id,
                            r.gesellschaft_id,
                            r.steuerarten,
                            r.normen,
                            _range(r.jahre),
                            r.offen,
                            r.modell,
                            r.vektor,
                            r.text_basis,
                        )
                        for r in records
                    ],
                )
            stored = await _embedding_keys(conn, gesellschaft, model)
            if sorted(stored) != expected:  # completeness gate for step 10
                raise RepositoryError(
                    f"embedding set incomplete: expected {len(expected)}, stored {len(stored)}"
                )
            await conn.execute(
                # A refresh after cleanup keeps state cleaned (states never move back).
                "UPDATE pipeline.project_state SET "
                "state = CASE WHEN state = 'cleaned' THEN 'cleaned' ELSE 'embedded' END, "
                "embedded_at = now(), embed_model = %s, embeddings_expected = %s, "
                "embeddings_written = %s, embeddings_fallback = %s, expected_features = %s, "
                "embeddings_stale = false, last_error = NULL, updated_at = now() "
                "WHERE company_id = %s",
                (model, len(expected), len(stored), fallback, Jsonb(expected), company_id),
            )

    async def list_embedding_keys(self, company_id: str, model: str) -> set[str]:
        async with await self._connection() as conn:
            return await _embedding_keys(conn, await _target_company(conn, company_id), model)

    async def list_embeddings(self, company_id: str, model: str) -> list[EmbeddingRecord]:
        async with await self._connection() as conn:
            gesellschaft = await _target_company(conn, company_id)
            cur = await conn.execute(
                "SELECT objekt_typ, objekt_id, gesellschaft_id, steuerarten::text[], normen, "
                "lower(jahre), upper(jahre) - 1, upper_inf(jahre), offen, modell, vektor, "
                "text_basis FROM profil.embedding WHERE gesellschaft_id = %s AND modell = %s",
                (gesellschaft, model),
            )
            return [
                EmbeddingRecord(
                    objekt_typ=r[0],
                    objekt_id=r[1],
                    gesellschaft_id=r[2],
                    steuerarten=r[3] or [],
                    normen=r[4] or [],
                    jahre=None if r[5] is None else (r[5], None if r[7] else r[6]),
                    offen=r[8],
                    modell=r[9],
                    vektor=r[10],
                    text_basis=r[11],
                )
                for r in await cur.fetchall()
            ]

    async def begin_step(self, company_id: str, step: str, timeout_s: float) -> None:
        async with await self._connection() as conn:
            cur = await conn.execute(
                "UPDATE pipeline.project_state SET running_step = %s, running_since = now() "
                "WHERE company_id = %s AND (running_step IS NULL "
                "OR running_since < now() - make_interval(secs => %s)) RETURNING 1",
                (step, company_id, timeout_s),
            )
            if await cur.fetchone() is not None:
                return
            cur = await conn.execute(
                "SELECT running_step FROM pipeline.project_state WHERE company_id = %s",
                (company_id,),
            )
            row = await cur.fetchone()
            if row is not None:
                raise BusyError(f"project '{company_id}' is busy ({row[0]} running); retry")

    async def end_step(self, company_id: str) -> None:
        async with await self._connection() as conn:
            await conn.execute(
                "UPDATE pipeline.project_state SET running_step = NULL, running_since = NULL "
                "WHERE company_id = %s",
                (company_id,),
            )

    async def update_profile(
        self, request: UpdateRequest, schema: ProfileSchema, settings: Settings, today: date
    ) -> UpdateResult:
        async with await self._connection() as conn, conn.transaction():
            return await update_profile(conn, request, schema, settings, today)

    async def read_profile(
        self, company_id: str, schema: ProfileSchema, settings: Settings
    ) -> ConfirmedProfile:
        async with await self._connection() as conn:
            return await read_profile(conn, company_id, schema, settings)

    async def list_audit(self, audit_filter: AuditFilter) -> list[AuditEntry]:
        async with await self._connection() as conn:
            return await read_audit(conn, audit_filter)

    async def delete_working_data(self, company_id: str) -> dict[str, int]:
        deleted = {}
        async with await self._connection() as conn, conn.transaction():
            for table in WORKING_TABLES:
                cur = await conn.execute(
                    f"DELETE FROM pipeline.{table} WHERE company_id = %s", (company_id,)
                )
                deleted[table] = cur.rowcount
            await conn.execute(
                "UPDATE pipeline.runs SET folder = NULL WHERE company_id = %s", (company_id,)
            )
        return deleted

    async def count_working_rows(self, company_id: str) -> dict[str, int]:
        counts = {}
        async with await self._connection() as conn:
            for table in WORKING_TABLES:
                cur = await conn.execute(
                    f"SELECT count(*) FROM pipeline.{table} WHERE company_id = %s", (company_id,)
                )
                (counts[table],) = await cur.fetchone()
        return counts

    async def mark_cleaned(self, company_id: str) -> None:
        async with await self._connection() as conn:
            cur = await conn.execute(
                "UPDATE pipeline.project_state SET state = 'cleaned', cleaned_at = now(), "
                "last_error = NULL, updated_at = now() "
                "WHERE company_id = %s AND state = 'embedded'",
                (company_id,),
            )
            if cur.rowcount != 1:
                raise StateError(f"project '{company_id}' is not embedded: cannot mark cleaned")

    # Step 0: runs of the upload layer -----------------------------------------------------

    async def ping(self) -> None:
        async with await self._connection() as conn:
            await conn.execute("SELECT 1")

    async def save_run(self, run: Run) -> None:
        r = run
        async with await self._connection() as conn:
            await conn.execute(
                _UPSERT_RUN,
                (
                    r.run_id,
                    r.company_id,
                    r.project_name,
                    str(r.state),
                    r.folder,
                    r.started_by,
                    r.created_at,
                    r.started_at,
                    r.finished_at,
                    Jsonb(r.counts),
                    r.published,
                    r.last_error,
                ),
            )

    async def get_run(self, run_id: str) -> Run | None:
        async with await self._connection() as conn, conn.cursor(row_factory=dict_row) as cur:
            await cur.execute("SELECT * FROM pipeline.runs WHERE run_id = %s", (run_id,))
            row = await cur.fetchone()
            return Run.model_validate(row) if row else None

    async def list_runs(self, company_id: str | None = None) -> list[Run]:
        async with await self._connection() as conn, conn.cursor(row_factory=dict_row) as cur:
            await cur.execute(
                "SELECT * FROM pipeline.runs WHERE %(c)s::text IS NULL OR company_id = %(c)s "
                "ORDER BY created_at DESC, run_id DESC",
                {"c": company_id},
            )
            return [Run.model_validate(row) for row in await cur.fetchall()]

    async def save_run_documents(self, documents: list[RunDocument]) -> None:
        if not documents:
            return
        async with await self._connection() as conn, conn.cursor() as cur:
            await cur.executemany(
                _UPSERT_RUN_DOCUMENT,
                [
                    (
                        d.run_id,
                        d.company_id,
                        d.relative_path,
                        d.doc_id,
                        d.parse_status,
                        d.extraction_status,
                        Jsonb(d.warnings),
                        d.pages,
                        d.seconds,
                        d.input_tokens,
                        d.output_tokens,
                    )
                    for d in documents
                ],
            )

    async def list_run_documents(self, run_id: str) -> list[RunDocument]:
        async with await self._connection() as conn, conn.cursor(row_factory=dict_row) as cur:
            await cur.execute(
                "SELECT * FROM pipeline.run_documents WHERE run_id = %s ORDER BY relative_path",
                (run_id,),
            )
            return [RunDocument.model_validate(row) for row in await cur.fetchall()]

    async def interrupt_active_runs(self, error: str) -> int:
        async with await self._connection() as conn:
            cur = await conn.execute(
                "UPDATE pipeline.runs SET state = 'interrupted', finished_at = now(), "
                "last_error = %s WHERE state IN ('queued', 'running')",
                (error,),
            )
            return cur.rowcount

    async def list_project_states(self) -> list[ProjectStatus]:
        async with await self._connection() as conn, conn.cursor(row_factory=dict_row) as cur:
            await cur.execute("SELECT * FROM pipeline.project_state ORDER BY company_id")
            return [ProjectStatus.model_validate(row) for row in await cur.fetchall()]

    async def company_exists(self, company_id: str) -> bool:
        async with await self._connection() as conn:
            cur = await conn.execute(
                "SELECT EXISTS (SELECT 1 FROM pipeline.project_state WHERE company_id = %(c)s) "
                "OR EXISTS (SELECT 1 FROM pipeline.runs WHERE company_id = %(c)s) "
                "OR EXISTS (SELECT 1 FROM pipeline.document_extractions WHERE company_id = %(c)s) "
                "OR EXISTS (SELECT 1 FROM pipeline.profile_drafts WHERE company_id = %(c)s) "
                "OR EXISTS (SELECT 1 FROM pipeline.publications WHERE company_id = %(c)s)",
                {"c": company_id},
            )
            (used,) = await cur.fetchone()
            if used:
                return True
            cur = await conn.execute("SELECT to_regclass('profil.deal') IS NOT NULL")
            (has_profil,) = await cur.fetchone()
            if not has_profil:
                return False
            cur = await conn.execute(
                "SELECT EXISTS (SELECT 1 FROM profil.deal WHERE projektname = %s)", (company_id,)
            )
            (used,) = await cur.fetchone()
            return used

    async def close(self) -> None:
        if self._opened:
            await self._pool.close()
            self._opened = False
