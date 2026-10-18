"""Postgres repository (psycopg 3, async, small pool). Works for Supabase and local Docker.

Our working tables live in schema `pipeline` (app/db/migrations); the profile goes into the
teammate's schema `profil` (backend/db_scheme). Supabase: use the direct or session
connection string. Client-side prepared statements are off, so the transaction-mode pooler
works too. On Windows, psycopg's async mode needs a selector event loop (the CLI and tests
set one up).
"""

import asyncio
import re
from datetime import UTC, date, datetime
from pathlib import Path

import psycopg
from psycopg.rows import dict_row
from psycopg.types.json import Jsonb
from psycopg_pool import AsyncConnectionPool

from app.config import Settings
from app.db.audit import read_audit, write_audit
from app.db.profil_reader import load_snapshot, target_company
from app.db.profil_updater import read_profile, update_profile
from app.db.profil_writer import delete_project_rows, publish_profile
from app.db.profile_sources import remove_profile_sources
from app.db.repository import (
    WORKING_TABLES,
    BusyError,
    DuplicateLawError,
    NotFoundError,
    PublishReport,
    Repository,
    RepositoryError,
    RunActiveError,
    SendPendingError,
    StaleDraftError,
    StaleProfileError,
    StateError,
)
from app.models.alert import SENDABLE, Alert, AlertStatus
from app.models.audit import AuditEntry, AuditFilter, AuditKind
from app.models.client import ClientInfo, MatchReview, MultiSendRequest
from app.models.extraction import Candidate, DocumentExtraction
from app.models.features import EmbeddingRecord, ProfileSnapshot
from app.models.job import Job
from app.models.law import Law, LawSection
from app.models.match import (
    FINAL_INVALIDATIONS,
    CompanyFeatures,
    Match,
    ScreeningChanges,
    ScreeningStatus,
)
from app.models.outreach import EmailTemplate, Outreach, default_template
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

_INSERT_LAW = """
INSERT INTO screening.law (kind, title, reference, published_at, effective_at, status,
    legal_forms, tax_types, summary, change_summary, filename, content_hash)
VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s) RETURNING id
"""

_INSERT_SECTION = """
INSERT INTO screening.law_section (law_id, nr, section_ref, heading, page_from, page_to, text,
    norms, tax_types, model, vector)
VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s::real[])
"""

_MATCH_COLUMNS = (
    "company_id",
    "gesellschaft_id",
    "law_id",
    "law_section_id",
    "objekt_typ",
    "objekt_id",
    "feature_text",
    "score",
    "norm_match",
    "form_unknown",
    "judge_relevant",
    "judge_direction",
    "judge_reason",
    "judge_confidence",
    "model",
    "trigger",
    "profile_version",
    "is_current",
    "input_fingerprint",
    "invalidation_reason",
)
_ENUM_COLUMNS = ("judge_direction", "trigger", "invalidation_reason")
_UPSERT_MATCH = f"""
INSERT INTO screening.match ({", ".join(_MATCH_COLUMNS)})
VALUES ({", ".join(["%s"] * len(_MATCH_COLUMNS))})
ON CONFLICT (company_id, law_section_id) DO UPDATE SET
    {", ".join(f"{c} = EXCLUDED.{c}" for c in _MATCH_COLUMNS[1:3] + _MATCH_COLUMNS[4:])},
    created_at = now()
RETURNING id
"""

_ALERT_SOURCE = """
SELECT jsonb_build_object('match', to_jsonb(m), 'law', to_jsonb(l),
    'section_ref', s.section_ref, 'section_text', s.text,
    'page_from', s.page_from, 'page_to', s.page_to)
FROM screening.match m JOIN screening.law l ON l.id = m.law_id
JOIN screening.law_section s ON s.id = m.law_section_id WHERE m.id = %s
"""

_ALERT_READ = """
SELECT a.*, COALESCE(to_jsonb(l), a.source_snapshot->'law') AS current_law,
       to_jsonb(m) AS current_match,
       COALESCE((SELECT g.ansprechpartner_email IS NOT NULL
           AND g.ansprechpartner_email <> '' FROM profil.gesellschaft g
           JOIN profil.deal d ON d.id = g.deal_id WHERE d.projektname = a.company_id
           ORDER BY g.ist_zielgesellschaft DESC, g.id LIMIT 1), false) AS recipient_available,
       COALESCE((SELECT jsonb_agg(to_jsonb(o) ORDER BY o.created_at DESC, o.id DESC)
           FROM screening.outreach o WHERE o.alert_id = a.id), '[]'::jsonb) AS outreach
FROM screening.alert a
LEFT JOIN screening.match m ON m.id = a.match_id
LEFT JOIN screening.law l ON l.id = m.law_id
"""


def _alert(row: dict) -> Alert:
    """The snapshot keeps the source after a law replacement; a live match row wins (its
    freshness decides whether the alert may be sent). Without one it is never current."""
    source = row.pop("source_snapshot")
    source["law"] = row.pop("current_law")
    current = row.pop("current_match")
    if current is not None:
        source["match"] = current
    else:
        source["match"]["is_current"] = False
    return Alert.model_validate(source | row)


_CLIENT_COLUMNS = (
    "company_id",
    "customer_number",
    "client_since",
    "notes",
    "contact_salutation",
    "contact_name",
    "contact_phone",
    "citations",
)

_JOB_COLUMNS = (
    "id",
    "operation",
    "company_id",
    "status",
    "dedupe_key",
    "params",
    "private",
    "result",
    "error",
    "retry_of",
    "created_at",
    "started_at",
    "finished_at",
)


def _job_values(job: Job) -> tuple:
    values = job.model_dump(mode="json")
    return tuple(
        Jsonb(values[c])
        if c in ("params", "private", "result") and values[c] is not None
        else getattr(job, c)
        if c.endswith("_at")
        else values[c]
        for c in _JOB_COLUMNS
    )


async def _busy(conn: psycopg.AsyncConnection, company_id: str, timeout_s: float) -> str | None:
    """An active job of the company or a fresh step marker blocks conflicting work."""
    cur = await conn.execute(
        "SELECT id, operation, status FROM pipeline.jobs "
        "WHERE company_id = %s AND status IN ('queued', 'running') LIMIT 1",
        (company_id,),
    )
    job = await cur.fetchone()
    if job is not None:
        return f"job {job[0]} ({job[1]}) is {job[2]}"
    cur = await conn.execute(
        "SELECT running_step FROM pipeline.project_state WHERE company_id = %s "
        "AND running_step IS NOT NULL "
        "AND running_since >= now() - make_interval(secs => %s)",
        (company_id, timeout_s),
    )
    step = await cur.fetchone()
    return f"{step[0]} is running" if step else None


async def _lock_mail_company(conn: psycopg.AsyncConnection, company_id: str) -> None:
    # Works for legacy alerts with no project state too. All send/review mutations take
    # this lock before state, reviews and alerts, preventing cross-API reservation races.
    await conn.execute("SELECT pg_advisory_xact_lock(hashtextextended(%s, 0))", (company_id,))
    await conn.execute(
        "SELECT 1 FROM pipeline.project_state WHERE company_id = %s FOR UPDATE", (company_id,)
    )


async def _sync_alert(cur: psycopg.AsyncCursor, match_id: int, match: Match) -> None:
    """Alert of one stored decision (same transitions as the memory repository): a current
    hit opens or revives the alert; a current negative or a final invalidation obsoletes a
    new/seen alert; a pending invalidation keeps the status (but blocks sending).
    Dismissed and actioned alerts never change."""
    await cur.execute(_ALERT_SOURCE, (match_id,))
    (source,) = await cur.fetchone()
    if match.judge_relevant and match.is_current:
        await cur.execute(
            "INSERT INTO screening.alert(match_id, company_id, source_snapshot) "
            "VALUES (%s, %s, %s) ON CONFLICT (match_id) DO UPDATE SET "
            "source_snapshot = EXCLUDED.source_snapshot, "
            "status = CASE WHEN alert.status = 'obsolete' THEN 'new' "
            "ELSE alert.status END, status_changed_at = CASE "
            "WHEN alert.status = 'obsolete' THEN now() "
            "ELSE alert.status_changed_at END",
            (match_id, match.company_id, Jsonb(source)),
        )
        return
    obsolete = match.is_current or match.invalidation_reason in FINAL_INVALIDATIONS
    await cur.execute(
        "UPDATE screening.alert SET source_snapshot = %s, "
        "status = CASE WHEN %s AND status IN ('new','seen') THEN 'obsolete' ELSE status END, "
        "status_changed_at = CASE WHEN %s AND status IN ('new','seen') THEN now() "
        "ELSE status_changed_at END WHERE match_id = %s",
        (Jsonb(source), obsolete, obsolete, match_id),
    )


# Vectors with their sentence and metadata; the year range comes back as an inclusive pair.
_EMBEDDINGS = """
SELECT objekt_typ, objekt_id, gesellschaft_id, steuerarten::text[], normen,
    lower(jahre), upper(jahre) - 1, upper_inf(jahre), offen, modell, vektor, text_basis
FROM profil.embedding WHERE gesellschaft_id = %s
"""


def _embedding_record(r: tuple) -> EmbeddingRecord:
    return EmbeddingRecord(
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
    def __init__(self, conninfo: str, max_size: int = 5, *, settings: Settings | None = None):
        self.settings = settings or Settings(_env_file=None)
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
            template = default_template(self.settings, datetime.now(UTC))
            await conn.execute(
                "INSERT INTO screening.email_template(id, subject, body) VALUES (1, %s, %s) "
                "ON CONFLICT (id) DO NOTHING",
                (template.subject, template.body),
            )

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
            cur = await conn.execute(_EMBEDDINGS + " AND modell = %s", (gesellschaft, model))
            return [_embedding_record(r) for r in await cur.fetchall()]

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

    async def apply_ui_update(
        self, company_id, version, request, info_patch, audit, schema, settings, today
    ):
        async with await self._connection() as conn, conn.transaction():
            cur = await conn.execute(
                "SELECT state, profile_version FROM pipeline.project_state "
                "WHERE company_id = %s FOR UPDATE",
                (company_id,),
            )
            state = await cur.fetchone()
            if state is None or state[0] not in EMBEDDABLE:
                raise StateError("profile is not confirmed")
            if state[1] != version:
                raise StaleProfileError("profile version is stale; reload the profile")
            busy = await _busy(conn, company_id, settings.step_lock_timeout_s)
            if busy:
                raise BusyError(busy)
            result = (
                await update_profile(conn, request, schema, settings, today) if request else None
            )
            if info_patch:
                columns = [
                    c
                    for c in _CLIENT_COLUMNS
                    if c in info_patch and c not in {"company_id", "citations"}
                ]
                await conn.execute(
                    "INSERT INTO pipeline.client_info(company_id) VALUES (%s) "
                    "ON CONFLICT (company_id) DO NOTHING",
                    (company_id,),
                )
                await conn.execute(
                    "UPDATE pipeline.client_info SET "
                    + ", ".join(f"{c} = %s" for c in columns)
                    + ", updated_at = now() WHERE company_id = %s",
                    (*[info_patch[c] for c in columns], company_id),
                )
                for entry in audit:
                    await write_audit(conn, entry)
                if request is None:
                    await conn.execute(
                        "UPDATE pipeline.project_state SET profile_version = profile_version + 1, "
                        "updated_at = now() WHERE company_id = %s",
                        (company_id,),
                    )
            return result

    async def read_profile(
        self, company_id: str, schema: ProfileSchema, settings: Settings
    ) -> ConfirmedProfile:
        async with await self._connection() as conn:
            return await read_profile(conn, company_id, schema, settings)

    async def list_audit(self, audit_filter: AuditFilter) -> list[AuditEntry]:
        async with await self._connection() as conn:
            return await read_audit(conn, audit_filter)

    async def remove_profile_sources(self, company_id: str) -> None:
        async with await self._connection() as conn, conn.transaction():
            await remove_profile_sources(conn, company_id)

    async def delete_working_data(self, company_id: str) -> dict[str, int]:
        deleted = {}
        async with await self._connection() as conn, conn.transaction():
            for table in WORKING_TABLES:
                cur = await conn.execute(
                    f"DELETE FROM pipeline.{table} WHERE company_id = %s", (company_id,)
                )
                deleted[table] = cur.rowcount
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
        async with await self._connection() as conn, conn.transaction():
            cur = await conn.execute(
                "UPDATE pipeline.project_state SET state = 'cleaned', "
                "cleaned_at = CASE WHEN state = 'embedded' THEN now() ELSE cleaned_at END, "
                "last_error = NULL, updated_at = now() "
                "WHERE company_id = %s AND state IN ('embedded', 'cleaned')",
                (company_id,),
            )
            if cur.rowcount != 1:
                raise StateError(f"project '{company_id}' is not embedded: cannot mark cleaned")
            await conn.execute(
                "UPDATE pipeline.runs SET folder = NULL WHERE company_id = %s", (company_id,)
            )

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

    # Steps 11 to 15: laws and screening -------------------------------------------------

    async def list_norm_codes(self) -> set[str]:
        async with await self._connection() as conn:
            cur = await conn.execute("SELECT to_regclass('profil.norm') IS NOT NULL")
            (has_norm,) = await cur.fetchone()
            if not has_norm:
                return set()
            cur = await conn.execute("SELECT code FROM profil.norm")
            return {row[0] for row in await cur.fetchall()}

    async def find_law(self, content_hash: str) -> Law | None:
        return next((x for x in await self.list_laws() if x.content_hash == content_hash), None)

    async def save_law(self, law: Law, sections: list[LawSection], *, replace: bool) -> int:
        async with await self._connection() as conn, conn.transaction():
            cur = await conn.execute(
                "SELECT id, title FROM screening.law WHERE content_hash = %s FOR UPDATE",
                (law.content_hash,),
            )
            old = await cur.fetchone()
            if old is not None and not replace:
                raise DuplicateLawError(f"law '{old[1]}' (id {old[0]}) is already stored")
            if old is not None:  # sections and matches go by cascade
                await conn.execute(
                    "UPDATE screening.alert a SET status = 'obsolete', status_changed_at = now() "
                    "FROM screening.match m WHERE m.id = a.match_id AND m.law_id = %s "
                    "AND a.status IN ('new','seen')",
                    (old[0],),
                )
                await conn.execute("DELETE FROM screening.law WHERE id = %s", (old[0],))
            cur = await conn.execute(
                _INSERT_LAW,
                (
                    str(law.kind),
                    law.title,
                    law.reference,
                    law.published_at,
                    law.effective_at,
                    str(law.status),
                    law.legal_forms,
                    list(law.tax_types),
                    law.summary,
                    law.change_summary,
                    law.filename,
                    law.content_hash,
                ),
            )
            (law_id,) = await cur.fetchone()
            async with conn.cursor() as cur:
                await cur.executemany(
                    _INSERT_SECTION,
                    [
                        (
                            law_id,
                            s.nr,
                            s.section_ref,
                            s.heading,
                            s.page_from,
                            s.page_to,
                            s.text,
                            s.norms,
                            s.tax_types,
                            s.model,
                            s.vector,
                        )
                        for s in sections
                    ],
                )
            return law_id

    async def list_laws(self) -> list[Law]:
        async with await self._connection() as conn, conn.cursor(row_factory=dict_row) as cur:
            await cur.execute(
                "SELECT l.*, (SELECT count(*) FROM screening.law_section s "
                "WHERE s.law_id = l.id) AS sections FROM screening.law l ORDER BY l.id"
            )
            return [Law.model_validate(row) for row in await cur.fetchall()]

    async def list_law_sections(self, law_id: int | None = None) -> list[LawSection]:
        async with await self._connection() as conn, conn.cursor(row_factory=dict_row) as cur:
            await cur.execute(
                "SELECT * FROM screening.law_section WHERE %(l)s::bigint IS NULL "
                "OR law_id = %(l)s ORDER BY law_id, nr",
                {"l": law_id},
            )
            return [LawSection.model_validate(row) for row in await cur.fetchall()]

    async def update_law_change_summary(self, law_id: int, change_summary: str | None) -> None:
        async with await self._connection() as conn:
            cur = await conn.execute(
                "UPDATE screening.law SET change_summary = %s WHERE id = %s",
                (change_summary, law_id),
            )
            if cur.rowcount != 1:
                raise RepositoryError(f"no law with id {law_id}")

    async def company_features(self, company_id: str) -> CompanyFeatures:
        async with await self._connection() as conn:
            gesellschaft = await _target_company(conn, company_id)
            cur = await conn.execute(
                "SELECT rechtsform FROM profil.gesellschaft WHERE id = %s", (gesellschaft,)
            )
            (rechtsform,) = await cur.fetchone()
            cur = await conn.execute(_EMBEDDINGS + " ORDER BY id", (gesellschaft,))
            return CompanyFeatures(
                company_id=company_id,
                gesellschaft_id=gesellschaft,
                rechtsform=rechtsform or None,
                features=[_embedding_record(r) for r in await cur.fetchall()],
            )

    async def list_matches(
        self,
        company_id: str,
        only_hits: bool = False,
        *,
        law_id: int | None = None,
        include_history: bool = False,
    ) -> list[Match]:
        async with await self._connection() as conn, conn.cursor(row_factory=dict_row) as cur:
            await cur.execute(
                "SELECT m.*, m.score::float8 AS score, s.section_ref, l.title AS law_title "
                "FROM screening.match m JOIN screening.law_section s ON s.id = m.law_section_id "
                "JOIN screening.law l ON l.id = m.law_id "
                "WHERE m.company_id = %(c)s AND (m.judge_relevant OR NOT %(h)s) "
                "AND (%(l)s::bigint IS NULL OR m.law_id = %(l)s) AND (m.is_current OR %(all)s) "
                "ORDER BY m.judge_relevant DESC, m.score DESC, m.law_section_id",
                {"c": company_id, "h": only_hits, "l": law_id, "all": include_history},
            )
            return [Match.model_validate(row) for row in await cur.fetchall()]

    async def save_matches(self, matches: list[Match]) -> None:
        if not matches:
            return
        async with await self._connection() as conn, conn.transaction(), conn.cursor() as cur:
            await self._upsert_matches(cur, matches)

    @staticmethod
    async def _upsert_matches(cur: psycopg.AsyncCursor, matches: list[Match]) -> None:
        for match in matches:
            values = tuple(
                str(v) if c in _ENUM_COLUMNS and v is not None else v
                for c, v in ((c, getattr(match, c)) for c in _MATCH_COLUMNS)
            )
            await cur.execute(_UPSERT_MATCH, values)
            (match_id,) = await cur.fetchone()
            await _sync_alert(cur, match_id, match)

    async def apply_screening(self, changes: ScreeningChanges) -> None:
        cid = changes.company_id
        if any(m.law_id not in changes.law_ids or m.company_id != cid for m in changes.judged):
            raise RepositoryError("judged match outside the screening scope")
        async with await self._connection() as conn, conn.transaction():
            async with conn.cursor() as cur:
                await self._upsert_matches(cur, changes.judged)
            updates = [(sid, True, None) for sid in changes.reused] + [
                (sid, False, str(reason)) for sid, reason in changes.invalidated.items()
            ]
            for section_id, current, reason in updates:
                async with conn.cursor(row_factory=dict_row) as cur:
                    await cur.execute(
                        "UPDATE screening.match SET is_current = %s, invalidation_reason = %s, "
                        "profile_version = CASE WHEN %s THEN %s ELSE profile_version END "
                        "WHERE company_id = %s AND law_section_id = %s "
                        "RETURNING *, score::float8 AS score",
                        (current, reason, current, changes.profile_version, cid, section_id),
                    )
                    row = await cur.fetchone()
                if row is not None:
                    async with conn.cursor() as cur:
                        await _sync_alert(cur, row["id"], Match.model_validate(row))
            for law_id, (deferred, unjudged) in changes.coverage.items():
                await conn.execute(
                    "INSERT INTO screening.coverage (company_id, law_id, complete, deferred, "
                    "unjudged, profile_version) VALUES (%s, %s, %s, %s, %s, %s) "
                    "ON CONFLICT (company_id, law_id) DO UPDATE SET complete = "
                    "EXCLUDED.complete, deferred = EXCLUDED.deferred, unjudged = "
                    "EXCLUDED.unjudged, profile_version = EXCLUDED.profile_version, "
                    "screened_at = now()",
                    (
                        cid,
                        law_id,
                        not (deferred or unjudged),
                        deferred,
                        unjudged,
                        changes.profile_version,
                    ),
                )
            if changes.screened_entries:
                await conn.execute(
                    "UPDATE pipeline.audit_log SET screened_at = now() "
                    "WHERE id = ANY(%s) AND screened_at IS NULL",
                    (changes.screened_entries,),
                )

    async def screening_status(self, company_id: str) -> ScreeningStatus:
        async with await self._connection() as conn:
            cur = await conn.execute(
                "SELECT "
                "(SELECT count(*) FROM pipeline.audit_log WHERE company_id = %(c)s "
                " AND kind = 'profile_changed' AND screened_at IS NULL), "
                "(SELECT coalesce(array_agg(law_id ORDER BY law_id), '{}') "
                " FROM screening.coverage WHERE company_id = %(c)s AND NOT complete), "
                "(SELECT coalesce(array_agg(l.id ORDER BY l.id), '{}') FROM screening.law l "
                " WHERE NOT EXISTS (SELECT 1 FROM screening.coverage v "
                " WHERE v.company_id = %(c)s AND v.law_id = l.id)), "
                "(SELECT count(*) FROM screening.match WHERE company_id = %(c)s "
                " AND NOT is_current AND invalidation_reason <> ALL(%(final)s))",
                {"c": company_id, "final": [str(r) for r in FINAL_INVALIDATIONS]},
            )
            pending, incomplete, unscreened, stale = await cur.fetchone()
        return ScreeningStatus(
            pending_changes=pending,
            incomplete_laws=incomplete,
            unscreened_laws=unscreened,
            stale_decisions=stale,
        )

    async def get_contact_email(self, company_id: str) -> str | None:
        async with await self._connection() as conn:
            company = await target_company(conn, company_id)
            if company is None:
                return None
            cur = await conn.execute(
                "SELECT ansprechpartner_email FROM profil.gesellschaft WHERE id = %s", (company,)
            )
            (email,) = await cur.fetchone()
            return email or None

    async def list_alerts(self, company_id=None, law_id=None) -> list[Alert]:
        async with await self._connection() as conn, conn.cursor(row_factory=dict_row) as cur:
            await cur.execute(
                _ALERT_READ + " WHERE (%(c)s::text IS NULL OR a.company_id = %(c)s) "
                "AND (%(l)s::bigint IS NULL OR "
                "(a.source_snapshot->'law'->>'id')::bigint = %(l)s)",
                {"c": company_id, "l": law_id},
            )
            return [_alert(row) for row in await cur.fetchall()]

    async def get_alert(self, alert_id: int) -> Alert:
        async with await self._connection() as conn, conn.cursor(row_factory=dict_row) as cur:
            await cur.execute(_ALERT_READ + " WHERE a.id = %s", (alert_id,))
            row = await cur.fetchone()
            if row is None:
                raise NotFoundError(f"no alert with id {alert_id}")
            return _alert(row)

    async def change_alert_status(self, alert_id, status, reason=None) -> Alert:
        allowed = (
            {AlertStatus.NEW}
            if status == AlertStatus.SEEN
            else SENDABLE
            if status == AlertStatus.DISMISSED
            else set()
        )
        async with await self._connection() as conn, conn.transaction():
            cur = await conn.execute(
                "SELECT status FROM screening.alert WHERE id = %s FOR UPDATE", (alert_id,)
            )
            row = await cur.fetchone()
            if row is None:
                raise NotFoundError(f"no alert with id {alert_id}")
            if row[0] not in allowed:
                raise StateError(f"alert {alert_id} is {row[0]}; cannot set {status}")
            cur = await conn.execute(
                "SELECT 1 FROM screening.outreach WHERE alert_id = %s AND status = 'pending'",
                (alert_id,),
            )
            if await cur.fetchone():
                raise StateError(f"alert {alert_id} has a pending send")
            await conn.execute(
                "UPDATE screening.alert SET status = %s, dismissed_reason = %s, "
                "status_changed_at = now() WHERE id = %s",
                (str(status), reason, alert_id),
            )
        return await self.get_alert(alert_id)

    async def get_template(self) -> EmailTemplate:
        async with await self._connection() as conn, conn.cursor(row_factory=dict_row) as cur:
            await cur.execute("SELECT * FROM screening.email_template WHERE id = 1")
            row = await cur.fetchone()
            if row is None:
                raise RepositoryError("email template missing; run --init-db")
            return EmailTemplate.model_validate(row)

    async def edit_template(self, subject: str, body: str, *, reset: bool = False) -> EmailTemplate:
        async with await self._connection() as conn, conn.transaction():
            cur = await conn.execute(
                "SELECT subject, body FROM screening.email_template WHERE id = 1 FOR UPDATE"
            )
            old = await cur.fetchone()
            if old is None:
                raise RepositoryError("email template missing; run --init-db")
            await conn.execute(
                "UPDATE screening.email_template SET subject = %s, body = %s, "
                "updated_at = clock_timestamp() WHERE id = 1",
                (subject, body),
            )
            await write_audit(
                conn,
                AuditEntry(
                    kind=AuditKind.TEMPLATE_EDITED,
                    acted_by=self.settings.advisor_label,
                    old_value={"subject": old[0], "body": old[1]},
                    new_value={"subject": subject, "body": body}
                    | ({"reset": True} if reset else {}),
                ),
            )
        return await self.get_template()

    async def reserve_outreach(self, request, delivered_to, delivery_subject) -> Outreach:
        from app.services.alerts import require_sendable

        async with await self._connection() as conn, conn.transaction():
            cur = await conn.execute(
                "SELECT company_id FROM screening.alert WHERE id = %s", (request.alert_id,)
            )
            found = await cur.fetchone()
            if found is None:
                raise NotFoundError(f"no alert with id {request.alert_id}")
            await _lock_mail_company(conn, found[0])
            cur = await conn.execute(
                "SELECT id FROM screening.outreach WHERE company_id = %s "
                "AND status = 'pending' LIMIT 1",
                (found[0],),
            )
            pending = await cur.fetchone()
            if pending:
                raise SendPendingError(
                    "Vorheriger Versand unklar; bitte zuerst auflösen.", pending[0]
                )
            cur = await conn.execute(
                "SELECT id FROM screening.alert WHERE id = %s FOR UPDATE", (request.alert_id,)
            )
            if await cur.fetchone() is None:
                raise NotFoundError(f"no alert with id {request.alert_id}")
            async with conn.cursor(row_factory=dict_row) as cur:
                await cur.execute(_ALERT_READ + " WHERE a.id = %s", (request.alert_id,))
                alert = _alert(await cur.fetchone())
                await cur.execute(
                    "SELECT * FROM pipeline.project_state WHERE company_id = %s",
                    (alert.company_id,),
                )
                state = await cur.fetchone()
                require_sendable(
                    alert,
                    alert.outreach,
                    ProjectStatus.model_validate(state) if state else None,
                    await _busy(conn, alert.company_id, self.settings.step_lock_timeout_s),
                )
                await cur.execute(
                    "INSERT INTO screening.outreach(alert_id, company_id, recipient, "
                    "delivered_to, subject, delivery_subject, sent_body, status, "
                    "template_updated_at) "
                    "SELECT %s, %s, %s, %s, %s, %s, %s, 'pending', COALESCE(%s, updated_at) "
                    "FROM screening.email_template WHERE id = 1 RETURNING *",
                    (
                        alert.id,
                        alert.company_id,
                        request.recipient,
                        delivered_to,
                        request.subject,
                        delivery_subject,
                        request.body,
                        request.template_updated_at,
                    ),
                )
                row = await cur.fetchone()
                if row is None:
                    raise RepositoryError("email template missing; run --init-db")
                return Outreach.model_validate(row)

    async def finish_outreach(
        self, outreach_id, status, *, message_id=None, error=None
    ) -> Outreach:
        async with await self._connection() as conn, conn.transaction():
            cur = await conn.execute(
                "SELECT alert_id, company_id, law_ids FROM screening.outreach WHERE id = %s",
                (outreach_id,),
            )
            found = await cur.fetchone()
            if found is None:
                raise NotFoundError(f"no outreach with id {outreach_id}")
            alert_id, company_id, law_ids = found
            await _lock_mail_company(conn, company_id)
            if alert_id is not None:
                # Alert first, same ordering as reserve/status actions, to avoid deadlocks.
                cur = await conn.execute(
                    "SELECT (source_snapshot->'law'->>'id')::bigint FROM screening.alert "
                    "WHERE id = %s FOR UPDATE",
                    (alert_id,),
                )
                law_ids = [(await cur.fetchone())[0]]
            else:  # one email for several laws: the reviews first, as in the reservation
                await conn.execute(
                    "SELECT 1 FROM screening.match_review WHERE company_id = %s "
                    "AND law_id = ANY(%s) ORDER BY law_id FOR UPDATE",
                    (company_id, law_ids),
                )
            async with conn.cursor(row_factory=dict_row) as cur:
                await cur.execute(
                    "SELECT * FROM screening.outreach WHERE id = %s FOR UPDATE", (outreach_id,)
                )
                row = Outreach.model_validate(await cur.fetchone())
                if row.status != "pending" or status not in {"sent", "failed"}:
                    raise StateError(f"outreach {outreach_id} is {row.status}; cannot set {status}")
                await cur.execute(
                    "UPDATE screening.outreach SET status = %s, error = %s, "
                    "provider_message_id = %s, sent_at = CASE WHEN %s = 'sent' THEN now() END "
                    "WHERE id = %s RETURNING *",
                    (status, error, message_id, status, outreach_id),
                )
                result = Outreach.model_validate(await cur.fetchone())
            await conn.execute(
                "UPDATE screening.match_review SET pending_outreach_id = NULL "
                "WHERE pending_outreach_id = %s",
                (outreach_id,),
            )
            if status == "sent":
                if alert_id is not None:
                    await conn.execute(
                        "UPDATE screening.alert SET status = 'actioned', "
                        "status_changed_at = now() WHERE id = %s",
                        (alert_id,),
                    )
                else:
                    await conn.execute(
                        "UPDATE screening.alert SET status = 'actioned', "
                        "status_changed_at = now() WHERE company_id = %s "
                        "AND (source_snapshot->'law'->>'id')::bigint = ANY(%s) "
                        "AND status IN ('new', 'seen')",
                        (company_id, law_ids),
                    )
                # The advisor's review follows every send from the app.
                await conn.execute(
                    "INSERT INTO screening.match_review (company_id, law_id, email_status, "
                    "sent_at, outreach_id, updated_at) "
                    "SELECT %s, l.id, 'sent', now(), %s, now() FROM screening.law l "
                    "WHERE l.id = ANY(%s) "
                    "ON CONFLICT (company_id, law_id) DO UPDATE SET email_status = 'sent', "
                    "sent_at = now(), outreach_id = EXCLUDED.outreach_id, updated_at = now()",
                    (company_id, outreach_id, law_ids),
                )
                await write_audit(
                    conn,
                    AuditEntry(
                        kind=AuditKind.EMAIL_SENT,
                        company_id=row.company_id,
                        acted_by=self.settings.advisor_label,
                        outreach_id=outreach_id,
                    ),
                )
            return result

    async def list_outreach(self, company_id=None, alert_id=None) -> list[Outreach]:
        async with await self._connection() as conn, conn.cursor(row_factory=dict_row) as cur:
            await cur.execute(
                "SELECT * FROM screening.outreach WHERE "
                "(%(c)s::text IS NULL OR company_id = %(c)s) "
                "AND (%(a)s::bigint IS NULL OR alert_id = %(a)s) ORDER BY created_at DESC, id DESC",
                {"c": company_id, "a": alert_id},
            )
            return [Outreach.model_validate(row) for row in await cur.fetchall()]

    # UI contract layer -------------------------------------------------------------------

    async def merkmal_names(self) -> dict[str, str]:
        async with await self._connection() as conn:
            cur = await conn.execute("SELECT code, bezeichnung FROM profil.merkmal_def")
            return dict(await cur.fetchall())

    async def get_client_info(self, company_id: str) -> ClientInfo | None:
        async with await self._connection() as conn, conn.cursor(row_factory=dict_row) as cur:
            await cur.execute(
                "SELECT * FROM pipeline.client_info WHERE company_id = %s", (company_id,)
            )
            row = await cur.fetchone()
            return ClientInfo.model_validate(row) if row else None

    async def save_client_info(self, info: ClientInfo, audit: list[AuditEntry] = ()) -> ClientInfo:
        values = info.model_dump(include=set(_CLIENT_COLUMNS))
        values["citations"] = Jsonb(values["citations"])
        async with await self._connection() as conn, conn.transaction():
            async with conn.cursor(row_factory=dict_row) as cur:
                await cur.execute(
                    f"INSERT INTO pipeline.client_info ({', '.join(_CLIENT_COLUMNS)}) "
                    f"VALUES ({', '.join(f'%({c})s' for c in _CLIENT_COLUMNS)}) "
                    "ON CONFLICT (company_id) DO UPDATE SET "
                    + ", ".join(f"{c} = EXCLUDED.{c}" for c in _CLIENT_COLUMNS[1:])
                    + ", updated_at = now() RETURNING *",
                    values,
                )
                stored = ClientInfo.model_validate(await cur.fetchone())
            for entry in audit:
                await write_audit(conn, entry)
        return stored

    async def save_client_note(self, company_id, notes, actor):
        async with await self._connection() as conn, conn.transaction():
            await conn.execute(
                "INSERT INTO pipeline.client_info(company_id) VALUES (%s) "
                "ON CONFLICT (company_id) DO NOTHING",
                (company_id,),
            )
            cur = await conn.execute(
                "SELECT notes FROM pipeline.client_info WHERE company_id = %s FOR UPDATE",
                (company_id,),
            )
            old = (await cur.fetchone())[0] or ""
            if old != notes:
                await conn.execute(
                    "UPDATE pipeline.client_info SET notes = %s, updated_at = now() "
                    "WHERE company_id = %s",
                    (notes, company_id),
                )
                await write_audit(
                    conn,
                    AuditEntry(
                        company_id=company_id,
                        kind=AuditKind.NOTE_CHANGED,
                        acted_by=actor,
                        field="notes",
                        old_value=old,
                        new_value=notes,
                        screened_at=datetime.now(UTC),
                    ),
                )

    async def append_audit(self, entry: AuditEntry) -> None:
        async with await self._connection() as conn, conn.transaction():
            await write_audit(conn, entry)

    async def list_match_reviews(
        self, company_id: str | None = None, law_id: int | None = None
    ) -> list[MatchReview]:
        async with await self._connection() as conn, conn.cursor(row_factory=dict_row) as cur:
            await cur.execute(
                "SELECT * FROM screening.match_review "
                "WHERE (%(c)s::text IS NULL OR company_id = %(c)s) "
                "AND (%(l)s::bigint IS NULL OR law_id = %(l)s) ORDER BY company_id, law_id",
                {"c": company_id, "l": law_id},
            )
            return [MatchReview.model_validate(row) for row in await cur.fetchall()]

    async def save_match_review(
        self, review: MatchReview, audit: list[AuditEntry] = ()
    ) -> MatchReview:
        async with await self._connection() as conn, conn.transaction():
            from app.services.alerts import validate_review_update

            await _lock_mail_company(conn, review.company_id)
            cur = await conn.execute(
                "SELECT EXISTS (SELECT 1 FROM screening.law WHERE id = %s)", (review.law_id,)
            )
            if not (await cur.fetchone())[0]:
                raise NotFoundError(f"no law with id {review.law_id}")
            cur = await conn.execute(
                "SELECT pending_outreach_id, outreach_id, email_status FROM screening.match_review "
                "WHERE company_id = %s AND law_id = %s FOR UPDATE",
                (review.company_id, review.law_id),
            )
            old = await cur.fetchone()
            validate_review_update(
                MatchReview(
                    company_id=review.company_id,
                    law_id=review.law_id,
                    pending_outreach_id=old[0],
                    outreach_id=old[1],
                    email_status=old[2],
                )
                if old
                else None,
                review,
            )
            if old is not None and old[0] is not None:
                raise SendPendingError(
                    f"the send of outreach {old[0]} is unclear; resolve it first", old[0]
                )
            async with conn.cursor(row_factory=dict_row) as cur:
                await cur.execute(
                    "INSERT INTO screening.match_review (company_id, law_id, label, "
                    "email_status, notes, sent_at, outreach_id, discussed_at, updated_at) "
                    "VALUES (%(company_id)s, %(law_id)s, %(label)s, %(email_status)s, "
                    "%(notes)s, %(sent_at)s, %(outreach_id)s, %(discussed_at)s, now()) "
                    "ON CONFLICT (company_id, law_id) DO UPDATE SET label = EXCLUDED.label, "
                    "email_status = EXCLUDED.email_status, notes = EXCLUDED.notes, "
                    "sent_at = EXCLUDED.sent_at, outreach_id = EXCLUDED.outreach_id, "
                    "discussed_at = EXCLUDED.discussed_at, updated_at = now() RETURNING *",
                    review.model_dump(),
                )
                stored = MatchReview.model_validate(await cur.fetchone())
            for entry in audit:
                await write_audit(conn, entry)
        return stored

    async def list_seen_laws(self) -> list[int]:
        async with await self._connection() as conn:
            cur = await conn.execute("SELECT law_id FROM screening.law_seen ORDER BY law_id")
            return [row[0] for row in await cur.fetchall()]

    async def mark_law_seen(self, law_id: int) -> None:
        async with await self._connection() as conn:
            try:
                await conn.execute(
                    "INSERT INTO screening.law_seen (law_id) VALUES (%s) "
                    "ON CONFLICT (law_id) DO NOTHING",
                    (law_id,),
                )
            except psycopg.errors.ForeignKeyViolation:
                raise NotFoundError(f"no law with id {law_id}") from None

    async def reserve_multi_outreach(
        self, request: MultiSendRequest, delivered_to: str, delivery_subject: str
    ) -> Outreach:
        from app.services.alerts import require_multi_sendable

        cid, law_ids = request.company_id, sorted(set(request.law_ids))
        async with (
            await self._connection() as conn,
            conn.transaction(),
            conn.cursor(row_factory=dict_row) as cur,
        ):
            await _lock_mail_company(conn, cid)
            await cur.execute(
                "SELECT * FROM screening.match_review WHERE company_id = %s "
                "AND law_id = ANY(%s) ORDER BY law_id FOR UPDATE",
                (cid, law_ids),
            )
            found = {r["law_id"]: MatchReview.model_validate(r) for r in await cur.fetchall()}
            await cur.execute(
                "SELECT id FROM screening.outreach WHERE company_id = %s "
                "AND status = 'pending' ORDER BY id LIMIT 1",
                (cid,),
            )
            pending = await cur.fetchone()
            await cur.execute(
                "SELECT DISTINCT law_id FROM screening.match WHERE company_id = %s "
                "AND is_current AND judge_relevant",
                (cid,),
            )
            current = {r["law_id"] for r in await cur.fetchall()}
            await cur.execute("SELECT * FROM pipeline.project_state WHERE company_id = %s", (cid,))
            state = await cur.fetchone()
            require_multi_sendable(
                cid,
                {law_id: found.get(law_id) for law_id in law_ids},
                pending["id"] if pending else None,
                current,
                ProjectStatus.model_validate(state) if state else None,
                await _busy(conn, cid, self.settings.step_lock_timeout_s),
            )
            await cur.execute(
                "INSERT INTO screening.outreach(alert_id, law_ids, company_id, recipient, "
                "delivered_to, subject, delivery_subject, sent_body, status, "
                "template_updated_at) "
                "SELECT NULL, %s, %s, %s, %s, %s, %s, %s, 'pending', updated_at "
                "FROM screening.email_template WHERE id = 1 RETURNING *",
                (
                    law_ids,
                    cid,
                    request.recipient,
                    delivered_to,
                    request.subject,
                    delivery_subject,
                    request.body,
                ),
            )
            row = await cur.fetchone()
            if row is None:
                raise RepositoryError("email template missing; run --init-db")
            outreach = Outreach.model_validate(row)
            await cur.execute(
                "UPDATE screening.match_review SET pending_outreach_id = %s "
                "WHERE company_id = %s AND law_id = ANY(%s)",
                (outreach.id, cid, law_ids),
            )
            return outreach

    async def discard_company(self, company_id: str) -> None:
        async with await self._connection() as conn, conn.transaction():
            cur = await conn.execute(
                "SELECT state FROM pipeline.project_state WHERE company_id = %s FOR UPDATE",
                (company_id,),
            )
            state = await cur.fetchone()
            if state is not None and state[0] != ProjectState.PUBLISHED:
                raise StateError(f"project '{company_id}' is {state[0]}: only drafts are discarded")
            cur = await conn.execute(
                "SELECT EXISTS (SELECT 1 FROM pipeline.runs WHERE company_id = %s "
                "AND state IN ('queued', 'running'))",
                (company_id,),
            )
            if (await cur.fetchone())[0]:
                raise RunActiveError(f"a run of '{company_id}' is still active; wait for it")
            cur = await conn.execute(
                "SELECT d.id FROM profil.deal d JOIN pipeline.publications p "
                "ON p.company_id = d.projektname WHERE d.projektname = %s",
                (company_id,),
            )
            deal = await cur.fetchone()
            if deal is not None:  # only rows the pipeline published, never a seed
                async with conn.cursor() as c:
                    await delete_project_rows(c, deal[0])
            for table in (*WORKING_TABLES, "runs", "project_state", "publications", "client_info"):
                await conn.execute(
                    f"DELETE FROM pipeline.{table} WHERE company_id = %s", (company_id,)
                )
            await conn.execute("DELETE FROM pipeline.jobs WHERE company_id = %s", (company_id,))

    async def pending_profile_changes(self, company_id: str) -> list[int]:
        async with await self._connection() as conn:
            cur = await conn.execute(
                "SELECT id FROM pipeline.audit_log WHERE company_id = %s "
                "AND kind = 'profile_changed' AND screened_at IS NULL ORDER BY id",
                (company_id,),
            )
            return [row[0] for row in await cur.fetchall()]

    async def mark_screened(self, entry_ids: list[int]) -> None:
        if not entry_ids:
            return
        async with await self._connection() as conn:
            await conn.execute(
                "UPDATE pipeline.audit_log SET screened_at = now() "
                "WHERE id = ANY(%s) AND screened_at IS NULL",
                (entry_ids,),
            )

    # Background jobs -------------------------------------------------------------------

    async def create_job(self, job: Job) -> Job:
        async with await self._connection() as conn:
            try:
                async with conn.transaction():  # a refused insert must not abort the read
                    await conn.execute(
                        f"INSERT INTO pipeline.jobs ({', '.join(_JOB_COLUMNS)}) "
                        f"VALUES ({', '.join(['%s'] * len(_JOB_COLUMNS))})",
                        _job_values(job),
                    )
            except psycopg.errors.UniqueViolation:
                cur = await conn.execute(
                    "SELECT id, operation FROM pipeline.jobs WHERE dedupe_key = %s "
                    "AND status IN ('queued', 'running')",
                    (job.dedupe_key,),
                )
                active = await cur.fetchone()
                which = f"job {active[0]} ({active[1]})" if active else "another job"
                raise BusyError(f"{which} is already active; wait for it") from None
        return job.model_copy(deep=True)

    async def save_job(self, job: Job) -> None:
        values = dict(zip(_JOB_COLUMNS, _job_values(job), strict=True))
        async with await self._connection() as conn:
            cur = await conn.execute(
                "UPDATE pipeline.jobs SET status = %(status)s, result = %(result)s, "
                "error = %(error)s, started_at = %(started_at)s, finished_at = %(finished_at)s "
                "WHERE id = %(id)s",
                values,
            )
            if cur.rowcount != 1:
                raise NotFoundError(f"no job with id {job.id}")

    async def get_job(self, job_id: str) -> Job | None:
        async with await self._connection() as conn, conn.cursor(row_factory=dict_row) as cur:
            await cur.execute("SELECT * FROM pipeline.jobs WHERE id = %s", (job_id,))
            row = await cur.fetchone()
            return Job.model_validate(row) if row else None

    async def list_jobs(
        self, company_id: str | None = None, *, retry_of: str | None = None
    ) -> list[Job]:
        async with await self._connection() as conn, conn.cursor(row_factory=dict_row) as cur:
            await cur.execute(
                "SELECT * FROM pipeline.jobs WHERE (%(c)s::text IS NULL OR company_id = %(c)s) "
                "AND (%(r)s::text IS NULL OR retry_of = %(r)s) ORDER BY created_at DESC, id DESC",
                {"c": company_id, "r": retry_of},
            )
            return [Job.model_validate(row) for row in await cur.fetchall()]

    async def interrupt_active_jobs(self, error: str) -> int:
        async with await self._connection() as conn:
            cur = await conn.execute(
                "UPDATE pipeline.jobs SET status = 'interrupted', finished_at = now(), "
                "error = %s WHERE status IN ('queued', 'running')",
                (error,),
            )
            return cur.rowcount

    async def clear_step_markers(self) -> int:
        async with await self._connection() as conn:
            cur = await conn.execute(
                "UPDATE pipeline.project_state SET running_step = NULL, running_since = NULL "
                "WHERE running_step IS NOT NULL"
            )
            return cur.rowcount

    async def company_busy(self, company_id: str, timeout_s: float) -> str | None:
        async with await self._connection() as conn:
            return await _busy(conn, company_id, timeout_s)

    async def close(self) -> None:
        if self._opened:
            await self._pool.close()
            self._opened = False
