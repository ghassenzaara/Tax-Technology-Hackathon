"""Plan section 4b: apply the advisor's manual updates to a confirmed profile in `profil`.

Reuses the writer (catalog, typed insert, row links, finding norms, hints), so one code path
writes `profil` rows. One transaction: state, busy marker and profile version are checked
under a row lock; each change runs in a savepoint so every problem is collected; any
problem rolls back everything. Changed or added rows get advisor provenance and the
confirmation stamp of this update; untouched rows are not modified. Each change gets one
`profile_changed` audit entry (all entries of the call share one update id), the profile
version goes up by one, and the vectors are marked stale until the embedding refresh
(services/update.py) has run. An edit that can change features (anything but the contact
address) also makes the company's screening decisions non-current until screening
validates them again (their alerts cannot be sent meanwhile).
"""

from datetime import UTC, date, datetime
from typing import Any
from uuid import uuid4

import psycopg
from psycopg import AsyncConnection
from psycopg.rows import dict_row

from app.config import Settings
from app.db.audit import read_audit, write_audit
from app.db.profil_reader import finding_norm_codes, target_company
from app.db.profil_writer import ADVISOR_SOURCE, SCHEMA, ProfilWriter, _label
from app.db.repository import (
    BusyError,
    PublishError,
    RepositoryError,
    StaleProfileError,
    StateError,
)
from app.models.audit import AuditEntry, AuditFilter, AuditKind
from app.models.profile import ProfileDraft
from app.models.project import Confirmation, ProjectState
from app.models.update import (
    Change,
    ChangeKind,
    ConfirmedProfile,
    DocumentCitation,
    ProfileRow,
    UpdateRequest,
    UpdateResult,
)
from app.profile.schema import FieldSpec, ProfileSchema
from app.profile.update import SCALAR_TABLES, UpdateError, check_request, error

FEATURE_TABLES = frozenset({"merkmal_wert", "finding", "rechtsbehelf"})  # embedded objects
_PROVENANCE = ("quelle_dok", "fundstelle", "stand", "stand_meta", "konfidenz", "gueltig_bis_meta")


class _Failed(Exception):
    """One change cannot be applied; the message goes next to the field."""


class ProfilUpdater(ProfilWriter):
    def __init__(
        self,
        conn: AsyncConnection,
        company_id: str,
        schema: ProfileSchema,
        settings: Settings,
        today: date,
        request: UpdateRequest | None = None,  # None: read only (profile read side)
    ):
        empty = ProfileDraft(company_id=company_id, fields=[], counts={}, schema_fields=[])
        super().__init__(conn, empty, [], schema, settings)
        self.company_id = company_id
        self.request = request
        self.today = today
        self.version = 0
        self.update_id = uuid4()  # shared by the audit entries of this call
        self.hints_closed = 0
        self.changed_features: set[str] = set()

    async def apply(self) -> UpdateResult:
        errors = check_request(self.request, self.schema)
        if errors:
            raise UpdateError(errors)
        await self._load_columns()
        state = await self._lock_state()
        await self._load_company()
        self.version = (state["profile_version"] or 1) + 1
        self.confirmation = Confirmation(
            by=self.request.changed_by, on=self.today, draft_version=state["draft_version"] or ""
        )
        for change in self.request.changes:
            try:
                async with self.conn.transaction():  # savepoint: collect every problem
                    await self._apply(change)
            except _Failed as exc:
                errors.append(error(change, str(exc)))
            except (PublishError, psycopg.Error) as exc:
                errors.append(error(change, _db_message(exc)))
        if errors:
            raise UpdateError(errors)
        # Contact addresses never contribute to feature sentences or judge input.
        # Editing/clearing one must not make otherwise valid vectors stale.
        affects_features = any(
            self.schema.field(change.field).format != "email" for change in self.request.changes
        )
        stale = state["embeddings_stale"] or (
            affects_features and state["state"] in (ProjectState.EMBEDDED, ProjectState.CLEANED)
        )
        if affects_features:  # decisions wait for screening; history and alerts are kept
            await self.conn.execute(
                "UPDATE screening.match SET is_current = false, "
                "invalidation_reason = 'profile_changed' WHERE company_id = %s AND is_current",
                (self.request.company_id,),
            )
        await self.conn.execute(
            "UPDATE pipeline.project_state SET profile_version = %s, "
            "embeddings_stale = embeddings_stale OR %s, updated_at = now() WHERE company_id = %s",
            (self.version, stale, self.request.company_id),
        )
        return UpdateResult(
            company_id=self.request.company_id,
            update_id=self.update_id,
            profile_version=self.version,
            changes=len(self.request.changes),
            hints_closed=self.hints_closed,
            changed_features=sorted(self.changed_features),
            embeddings_stale=stale,
        )

    # Checks and context ----------------------------------------------------------------

    async def _lock_state(self) -> dict[str, Any]:
        """State must be confirmed or later, no step or background job running (a step
        marker older than the stale-lock timeout counts as abandoned), version as seen by
        the UI."""
        cid = self.request.company_id
        async with self.conn.cursor(row_factory=dict_row) as cur:
            await cur.execute(
                "SELECT state, profile_version, draft_version, embeddings_stale, "
                "CASE WHEN running_since >= now() - make_interval(secs => %s) "
                "THEN running_step END AS running_step "
                "FROM pipeline.project_state WHERE company_id = %s FOR UPDATE",
                (self.settings.step_lock_timeout_s, cid),
            )
            state = await cur.fetchone()
            await cur.execute(
                "SELECT id, operation FROM pipeline.jobs WHERE company_id = %s "
                "AND status IN ('queued', 'running') LIMIT 1",
                (cid,),
            )
            job = await cur.fetchone()
        if state is None or state["state"] == ProjectState.PUBLISHED:
            current = state["state"] if state else "not published"
            raise StateError(
                f"project '{cid}' is {current}: updates need a confirmed profile "
                "(before confirmation the review decides)"
            )
        if state["running_step"]:
            raise BusyError(f"project '{cid}' is busy ({state['running_step']} running); retry")
        if job is not None:
            raise BusyError(f"project '{cid}' is busy (job {job['id']} {job['operation']}); retry")
        if (state["profile_version"] or 1) != self.request.profile_version:
            raise StaleProfileError(
                f"profile version {self.request.profile_version} is stale "
                f"(current: {state['profile_version'] or 1}); reload the profile"
            )
        return state

    async def _load_company(self) -> None:
        """Ids and existing link targets, so added rows link like a publish."""
        cid = self.company_id
        gesellschaft = await target_company(self.conn, cid)
        if gesellschaft is None:
            raise RepositoryError(f"project '{cid}' has no target company in {SCHEMA}")
        async with self.conn.cursor(row_factory=dict_row) as cur:
            await cur.execute(
                f"SELECT g.id, g.name, g.deal_id FROM {SCHEMA}.gesellschaft g WHERE g.id = %s",
                (gesellschaft,),
            )
            row = await cur.fetchone()
            self.gesellschaft_id, self.gesellschaft_name, self.deal_id = (
                row["id"],
                row["name"],
                row["deal_id"],
            )
            await cur.execute(
                f"SELECT id, jahr, steuerart::text FROM {SCHEMA}.veranlagung "
                "WHERE gesellschaft_id = %s",
                (gesellschaft,),
            )
            self.links["veranlagung"] = {
                (r["jahr"], r["steuerart"]): r["id"] for r in await cur.fetchall()
            }
            await cur.execute(
                f"SELECT id, zeitraum_von AS von, zeitraum_bis AS bis, status "
                f"FROM {SCHEMA}.aussenpruefung WHERE gesellschaft_id = %s",
                (gesellschaft,),
            )
            self.audits = list(await cur.fetchall())
            await cur.execute(
                f"SELECT id, finding_code FROM {SCHEMA}.finding WHERE deal_id = %s",
                (self.deal_id,),
            )
            self.links["finding"] = {(r["finding_code"],): r["id"] for r in await cur.fetchall()}

    # One change ------------------------------------------------------------------------

    async def _apply(self, change: Change) -> None:
        spec = self.schema.field(change.field)
        table = spec.table or spec.name
        if not spec.is_list:
            await self._edit_scalar(spec, table, change)
            return
        if change.kind is ChangeKind.ADD:
            await self._add_row(spec, table, change)
            return
        row = await self._find_row(spec, table, change)
        key = {k: row.get(k) for k in spec.key or [] if k in row}
        if change.kind is ChangeKind.REMOVE:
            await self.conn.execute(f"DELETE FROM {SCHEMA}.{table} WHERE id = %s", (row["id"],))
            await self._record(spec, table, change, row["id"], key, row, None)
        elif change.kind is ChangeKind.END:
            if "gueltig_bis" not in self.columns[table]:
                # TODO: list exactly which tables support ending an entry.
                raise _Failed(f"{table} rows have no validity end; edit or remove instead")
            old = {"gueltig_bis": row.get("gueltig_bis")}
            await self._update(table, row["id"], {"gueltig_bis": change.end_date})
            await self._record(
                spec, table, change, row["id"], key, old, {"gueltig_bis": change.end_date}
            )
        else:
            cells = {k: v for k, v in change.cells.items()}
            self._check_columns(table, cells)
            await self._update(table, row["id"], cells)
            old = {k: row.get(k) for k in cells}
            await self._record(spec, table, change, row["id"], key, old, cells)
        await self._close_hints(f"{spec.name} {_label(key)}")
        if table in FEATURE_TABLES:
            self.changed_features.add(f"{table}:{row['id']}")

    async def _edit_scalar(self, spec: FieldSpec, table: str, change: Change) -> None:
        self._check_columns(table, {spec.name: change.value})
        row_id = self.deal_id if table == "deal" else self.gesellschaft_id
        old = await self._select(
            f"SELECT {spec.name} AS v FROM {SCHEMA}.{table} WHERE id = %s", row_id
        )
        values: dict[str, Any] = {spec.name: change.value}
        if table == "gesellschaft":
            # One citation covers the whole row: keep it and note the advisor's change.
            note = f"; {spec.name} {ADVISOR_SOURCE.lower()} am {self.today}"
            values |= self._stamp(table)
            await self.conn.execute(
                f"UPDATE {SCHEMA}.gesellschaft SET fundstelle = left(coalesce(fundstelle, '') "
                "|| %s, 2000) WHERE id = %s",
                (note, row_id),
            )
        await self._set(table, row_id, values)
        await self._record(spec, table, change, None, None, old["v"], change.value)
        await self._close_hints(spec.name)

    async def _add_row(self, spec: FieldSpec, table: str, change: Change) -> None:
        value = {i.name: change.cells.get(i.name) for i in spec.items or []}
        row = {
            i.name: value[i.name] for i in spec.items or [] if i.db and value[i.name] is not None
        }
        self._check_columns(table, row)
        label = f"{spec.name} {_label({k: value.get(k) for k in spec.key or []})}"
        hints = self.report.hints
        parent = await self._parent(spec, table, value, row, label)
        if parent is None or self.report.hints != hints:
            raise _Failed("the row cannot be linked (for example no tax audit to attach it to)")
        row |= parent | self._advisor_provenance(table)
        row_id = await self._insert(table, row)  # stamps bestaetigt_von/_am (confirmation)
        await self._after_insert(table, value, row_id)
        key = {k: value.get(k) for k in spec.key or []}
        await self._record(spec, table, change, row_id, key, None, change.cells)
        await self._close_hints(label)  # for example a row the publish had to skip
        if table in FEATURE_TABLES:
            self.changed_features.add(f"{table}:{row_id}")

    # SQL helpers -----------------------------------------------------------------------

    async def _find_row(self, spec: FieldSpec, table: str, change: Change) -> dict[str, Any]:
        """The row of this company identified by row_id or key values (exactly one)."""
        where, params = self._owner(spec)
        if change.row_id is not None:
            where += " AND t.id = %s"
            params.append(change.row_id)
        else:
            for column, value in (change.key or {}).items():
                if column not in self.columns[table]:
                    raise _Failed(f"key column {column} is not stored; use row_id")
                where += f" AND t.{column} IS NOT DISTINCT FROM %s::{self.columns[table][column]}"
                params.append(value)
        async with self.conn.cursor() as cur:
            await cur.execute(
                f"SELECT row_to_json(t) FROM {SCHEMA}.{table} t WHERE {where}", params
            )
            rows = [r[0] for r in await cur.fetchall()]
        if len(rows) != 1:
            raise _Failed("no such row" if not rows else "the key matches several rows; use row_id")
        return rows[0]

    def _owner(self, spec: FieldSpec) -> tuple[str, list[Any]]:
        """Rows of a list table that belong to this company."""
        column = spec.parent_column or "gesellschaft_id"
        if column == "deal_id":
            return "t.deal_id = %s", [self.deal_id]
        if column == "aussenpruefung_id":
            return (
                f"t.aussenpruefung_id IN (SELECT id FROM {SCHEMA}.aussenpruefung "
                "WHERE gesellschaft_id = %s)",
                [self.gesellschaft_id],
            )
        return f"t.{column} = %s", [self.gesellschaft_id]

    def _check_columns(self, table: str, values: dict[str, Any]) -> None:
        problems = [
            f"{column}: {message}"
            for column, value in values.items()
            if (message := self._column_error(table, column, value))
        ]
        if problems:
            raise _Failed("; ".join(problems))

    def _advisor_provenance(self, table: str) -> dict[str, Any]:
        """Same rule as a typed value in step 8: the advisor is the source."""
        columns = self.columns[table]
        date_column = "stand_meta" if "stand_meta" in columns else "stand"
        provenance = {"fundstelle": f"{ADVISOR_SOURCE} am {self.today}", "konfidenz": "hoch"}
        if date_column in columns:
            provenance[date_column] = self.today
        return {k: v for k, v in provenance.items() if k in columns}

    def _stamp(self, table: str) -> dict[str, Any]:
        if "bestaetigt_von" not in self.columns[table]:
            return {}
        return {"bestaetigt_von": self.confirmation.by, "bestaetigt_am": self.today}

    async def _update(self, table: str, row_id: int, cells: dict[str, Any]) -> None:
        """Changed list row: new cells, advisor provenance (quelle_dok cleared), stamp."""
        values = cells | self._advisor_provenance(table) | self._stamp(table)
        if "quelle_dok" in self.columns[table]:
            values["quelle_dok"] = None
        await self._set(table, row_id, values)

    async def _set(self, table: str, row_id: int, values: dict[str, Any]) -> None:
        columns = self.columns[table]
        assignments = ", ".join(f"{name} = %s::{columns[name]}" for name in values)
        await self.conn.execute(
            f"UPDATE {SCHEMA}.{table} SET {assignments} WHERE id = %s",
            [*values.values(), row_id],
        )

    async def _select(self, sql: str, *params: Any) -> dict[str, Any]:
        async with self.conn.cursor(row_factory=dict_row) as cur:
            await cur.execute(sql, params)
            return await cur.fetchone()

    async def _close_hints(self, feld: str) -> None:
        cur = await self.conn.execute(
            f"UPDATE {SCHEMA}.pruefhinweis SET status = 'geklaert', entscheidung = %s "
            "WHERE gesellschaft_id = %s AND status = 'offen' AND feld = %s",
            (
                f"Vom Steuerberater geändert am {self.today} (von {self.confirmation.by})",
                self.gesellschaft_id,
                feld[:200],
            ),
        )
        self.hints_closed += cur.rowcount

    async def _record(
        self,
        spec: FieldSpec,
        table: str,
        change: Change,
        row_id: int | None,
        key: dict[str, Any] | None,
        old: Any,
        new: Any,
    ) -> None:
        """One `profile_changed` audit entry per change (profile values only)."""
        await write_audit(
            self.conn,
            AuditEntry(
                kind=AuditKind.PROFILE_CHANGED,
                company_id=self.request.company_id,
                update_id=self.update_id,
                acted_by=self.request.changed_by,
                table_name=table,
                field=spec.name,
                row_id=row_id,
                row_key=key or None,
                change_kind=str(change.kind),
                old_value=old,
                new_value=new,
                reason=self.request.reason,
                profile_version=self.version,
                # A contact address never reaches features or the judge: nothing to re-screen.
                screened_at=datetime.now(UTC) if spec.format == "email" else None,
            ),
        )


async def update_profile(
    conn: AsyncConnection,
    request: UpdateRequest,
    schema: ProfileSchema,
    settings: Settings,
    today: date,
) -> UpdateResult:
    """Run inside a transaction: all changes or none."""
    updater = ProfilUpdater(conn, request.company_id, schema, settings, today, request)
    return await updater.apply()


async def read_profile(
    conn: AsyncConnection, company_id: str, schema: ProfileSchema, settings: Settings
) -> ConfirmedProfile:
    """Confirmed values with their citations, the profile version and the audit log."""
    async with conn.cursor(row_factory=dict_row) as cur:
        await cur.execute(
            "SELECT state, profile_version, embeddings_stale FROM pipeline.project_state "
            "WHERE company_id = %s",
            (company_id,),
        )
        state = await cur.fetchone()
    gesellschaft = await target_company(conn, company_id)
    if state is None or gesellschaft is None:
        raise StateError(f"project '{company_id}' has no published profile")
    updater = ProfilUpdater(conn, company_id, schema, settings, date.today())
    await updater._load_columns()
    await updater._load_company()
    async with conn.cursor(row_factory=dict_row) as cur:
        await cur.execute(
            f"SELECT id, dateiname, titel, klasse_code, dokument_datum::text AS dokument_datum "
            f"FROM {SCHEMA}.dokument WHERE deal_id = %s",
            (updater.deal_id,),
        )
        documents = {r["id"]: DocumentCitation.model_validate(r) for r in await cur.fetchall()}
    scalars, scalar_rows = {}, {}
    for table in SCALAR_TABLES:
        row_id = updater.deal_id if table == "deal" else updater.gesellschaft_id
        row = await _row_json(
            conn, f"SELECT row_to_json(t) FROM {SCHEMA}.{table} t WHERE t.id = %s", [row_id]
        )
        scalar_rows[table] = _profile_row(row, documents)
        names = [s.name for s in schema.fields if not s.is_list and s.table == table]
        scalars |= {name: row.get(name) for name in names}
    # Association tables come back as the list cell the review showed (sorted codes).
    norms = await finding_norm_codes(conn, updater.deal_id)
    lists = {}
    for spec in schema.fields:
        if not spec.is_list:
            continue
        table = spec.table or spec.name
        where, params = updater._owner(spec)
        rows = await _rows_json(
            conn,
            f"SELECT row_to_json(t) FROM {SCHEMA}.{table} t WHERE {where} ORDER BY t.id",
            params,
        )
        if table == "finding":
            rows = [r | {"normen": norms.get(r["id"], [])} for r in rows]
        lists[spec.name] = [_profile_row(r, documents) for r in rows]
    return ConfirmedProfile(
        company_id=company_id,
        state=state["state"],
        profile_version=state["profile_version"],
        embeddings_stale=state["embeddings_stale"],
        scalars=scalars,
        scalar_rows=scalar_rows,
        lists=lists,
        audit=await read_audit(conn, AuditFilter(company_id=company_id)),
    )


def _profile_row(row: dict[str, Any], documents: dict[int, DocumentCitation]) -> ProfileRow:
    meta = {"id", *_PROVENANCE, "bestaetigt_von", "bestaetigt_am"}
    return ProfileRow(
        id=row["id"],
        values={k: v for k, v in row.items() if k not in meta},
        fundstelle=row.get("fundstelle"),
        quelle=documents.get(row.get("quelle_dok")),
        konfidenz=row.get("konfidenz"),
        bestaetigt_von=row.get("bestaetigt_von"),
        bestaetigt_am=row.get("bestaetigt_am"),
    )


async def _rows_json(conn: AsyncConnection, sql: str, params: list[Any]) -> list[dict]:
    cur = await conn.execute(sql, params)
    return [r[0] for r in await cur.fetchall()]


async def _row_json(conn: AsyncConnection, sql: str, params: list[Any]) -> dict:
    rows = await _rows_json(conn, sql, params)
    return rows[0]


def _db_message(exc: Exception) -> str:
    if isinstance(exc, psycopg.errors.ForeignKeyViolation):
        return "the row is still referenced by other rows; change or remove those first"
    if isinstance(exc, psycopg.errors.UniqueViolation):
        return "a row with these values already exists"
    diag = getattr(exc, "diag", None)
    return (diag.message_primary if diag else None) or str(exc)
