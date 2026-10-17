"""Plan section 4b, plain code: check an update request against the profile schema.

Same field-type rules and per-field messages as the typed values of step 8. Checks that
need the database (row exists, column length, links, references) run in the updater, in
the same transaction. Every problem is returned at once; nothing is applied on any error.
"""

from datetime import date
from typing import Any

from app.models.review import FieldError
from app.models.update import Change, ChangeKind, UpdateRequest
from app.profile.review import DecisionError, typed_value_error
from app.profile.schema import FieldSpec, ProfileSchema

SCALAR_TABLES = ("deal", "gesellschaft")
MERKMAL_TABLE = "merkmal_wert"
# Columns written by code, never by an update (identity, links, provenance).
PROTECTED = frozenset(
    {
        "id",
        "deal_id",
        "gesellschaft_id",
        "projektname",
        "quelle_dok",
        "fundstelle",
        "stand_meta",
        "konfidenz",
        "bestaetigt_von",
        "bestaetigt_am",
        "gueltig_bis_meta",
    }
)


class UpdateError(DecisionError):
    """The update cannot be applied. `errors` lists every problem per field."""


def change_target(change: Change) -> dict[str, Any] | None:
    """Row identity shown next to an error: the key, or the row id."""
    if change.key is not None:
        return change.key
    return {"row_id": change.row_id} if change.row_id is not None else None


def error(change: Change, message: str) -> FieldError:
    return FieldError(field=change.field, key=change_target(change), message=message)


def check_request(request: UpdateRequest, schema: ProfileSchema) -> list[FieldError]:
    """Schema-level problems of every change (empty list = structurally valid)."""
    specs = {s.name: s for s in schema.fields}
    errors: list[FieldError] = []
    for change in request.changes:
        spec = specs.get(change.field)
        if spec is None:
            errors.append(error(change, "unknown field"))
        elif spec.is_list:
            errors += _check_row(spec, change)
        else:
            errors += _check_scalar(spec, change)
    return errors


def _check_scalar(spec: FieldSpec, change: Change) -> list[FieldError]:
    if change.kind is not ChangeKind.EDIT:
        return [error(change, f"a scalar field can only be edited, not {change.kind}")]
    if spec.table not in SCALAR_TABLES or spec.name in PROTECTED:
        return [error(change, "this field cannot be changed")]
    if change.value is None:
        return [error(change, "a required field cannot be emptied")] if spec.required else []
    message = typed_value_error(spec, change.value)
    return [error(change, message)] if message else []


def _check_row(spec: FieldSpec, change: Change) -> list[FieldError]:
    errors = []
    adding = change.kind is ChangeKind.ADD
    if adding and (change.row_id is not None or change.key is not None):
        errors.append(error(change, "a new row takes no row_id or key"))
    if not adding and change.row_id is None and change.key is None:
        errors.append(error(change, "give the row_id or the key of the row"))
    if change.kind is ChangeKind.REMOVE and spec.table == MERKMAL_TABLE:
        errors.append(error(change, "a Merkmal is never removed; set wert to unbekannt or nein"))
    if change.kind is ChangeKind.END:
        try:
            date.fromisoformat(change.end_date or "")
        except ValueError:
            errors.append(error(change, "end needs end_date as ISO date (YYYY-MM-DD)"))
    if change.kind in (ChangeKind.EDIT, ChangeKind.ADD):
        if not change.cells:
            errors.append(error(change, f"{change.kind} needs cells"))
        columns = {i.name: i for i in spec.items or []}
        for name, value in change.cells.items():
            column = columns.get(name)
            if column is None or name in PROTECTED:
                errors.append(error(change, f"unknown column {name}"))
            elif not column.db and not adding:
                errors.append(error(change, f"{name}: links are set when the row is added"))
            elif value is None:
                if column.required:
                    errors.append(error(change, f"{name}: a required field cannot be emptied"))
            elif message := typed_value_error(column, value):
                errors.append(error(change, f"{name}: {message}"))
        if adding:
            missing = [
                i.name for i in spec.items or [] if i.required and change.cells.get(i.name) is None
            ]
            if missing:
                errors.append(error(change, f"a new row needs {', '.join(missing)}"))
    return errors
