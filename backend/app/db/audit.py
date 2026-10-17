"""The one write function for every audit kind, and the filtered read (plan section 4b.4).

There is no update or delete path in the code; the database trigger of migration
004_audit_log.sql rejects them (except matching setting `screened_at` once).
"""

import json
from typing import Any

from psycopg import AsyncConnection
from psycopg.rows import dict_row
from psycopg.types.json import Jsonb

from app.models.audit import AuditEntry, AuditFilter

_COLUMNS = (
    "kind",
    "company_id",
    "update_id",
    "acted_by",
    "table_name",
    "field",
    "row_id",
    "row_key",
    "change_kind",
    "old_value",
    "new_value",
    "reason",
    "profile_version",
    "outreach_id",
)
_JSON = frozenset({"row_key", "old_value", "new_value"})


def jsonable(value: Any) -> Any:
    """Dates and decimals as JSON text (as they come from rows or requests)."""
    return json.loads(json.dumps(value, default=str))


def _value(entry: AuditEntry, column: str) -> Any:
    value = getattr(entry, column)
    if value is None:
        return None
    if column in _JSON:
        return Jsonb(jsonable(value))
    return str(value) if column == "kind" else value


async def write_audit(conn: AsyncConnection, entry: AuditEntry) -> int:
    """Append one entry inside the caller's transaction; returns its id."""
    values = [_value(entry, column) for column in _COLUMNS]
    placeholders = ", ".join(["%s"] * len(_COLUMNS))
    cur = await conn.execute(
        f"INSERT INTO pipeline.audit_log ({', '.join(_COLUMNS)}) VALUES ({placeholders}) "
        "RETURNING id",
        values,
    )
    (entry_id,) = await cur.fetchone()
    return entry_id


async def read_audit(conn: AsyncConnection, audit_filter: AuditFilter) -> list[AuditEntry]:
    """Entries matching every given filter, newest first."""
    f = audit_filter
    where, params = ["true"], []
    for column, value in (
        ("company_id", f.company_id),
        ("kind", str(f.kind) if f.kind else None),
        ("acted_by", f.acted_by),
    ):
        if value is not None:
            where.append(f"{column} = %s")
            params.append(value)
    if f.since is not None:
        where.append("acted_at >= %s")
        params.append(f.since)
    if f.until is not None:
        where.append("acted_at <= %s")
        params.append(f.until)
    async with conn.cursor(row_factory=dict_row) as cur:
        await cur.execute(
            f"SELECT * FROM pipeline.audit_log WHERE {' AND '.join(where)} "
            "ORDER BY acted_at DESC, id DESC LIMIT %s",
            [*params, f.limit],
        )
        return [AuditEntry.model_validate(row) for row in await cur.fetchall()]
