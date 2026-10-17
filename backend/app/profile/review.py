"""Step 8, plain code: which fields need a decision and how decisions change the draft.

The backend, not only the UI, decides what is open, so the rule holds even when a client
is wrong. Open: every scalar conflict, every scalar with nothing found, and every list row
whose candidate groups disagree on a cell. Confirmed and single-source values need no
decision; confirming the profile accepts them. A suggestion is never applied as a decision.

`apply_decisions` returns the confirmed draft (each decided field carries an
`AppliedDecision`) and the counts for the confirm result, or raises `DecisionError` listing
every problem at once.
"""

import hashlib
import json
import math
from datetime import date
from typing import Any

from app.models.profile import (
    AppliedDecision,
    CandidateGroup,
    DecisionAction,
    FieldResult,
    FieldStatus,
    ProfileDraft,
)
from app.models.review import ConfirmRequest, ConfirmResult, Decision, FieldError, OpenDecision
from app.profile.schema import FieldSpec, FieldType, ProfileSchema
from app.profile.values import values_equal

SCALAR_ACTIONS = {
    FieldStatus.CONFLICT: [DecisionAction.CHOOSE, DecisionAction.VALUE, DecisionAction.LEAVE_EMPTY],
    FieldStatus.MISSING: [DecisionAction.VALUE, DecisionAction.LEAVE_EMPTY],
}
ROW_CONFLICT_ACTIONS = [DecisionAction.EDIT, DecisionAction.DROP]
ROW_ACTIONS = [DecisionAction.ACCEPT, DecisionAction.EDIT, DecisionAction.DROP]


class DecisionError(ValueError):
    """The decisions cannot confirm the draft. `errors` lists every problem per field."""

    def __init__(self, errors: list[FieldError]):
        self.errors = errors
        super().__init__("; ".join(_error_text(e) for e in errors))


def draft_version(draft: ProfileDraft) -> str:
    """Stable identifier of exactly this draft (content hash, key order independent)."""
    payload = json.dumps(draft.model_dump(mode="json"), sort_keys=True, ensure_ascii=False)
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()[:16]


def key_text(key: dict[str, Any] | None) -> str:
    """Canonical form of a list row key, for matching decisions to rows."""
    return json.dumps(key or {}, sort_keys=True, ensure_ascii=False, default=str)


def sources_text(group: CandidateGroup) -> str:
    """Citation of a group: file names and pages only, never the evidence quote."""
    return ", ".join(f"{c.source_filename} S. {c.source_page}" for c in group.candidates)


def short(value: Any) -> str:
    text = json.dumps(value, ensure_ascii=False) if not isinstance(value, str) else value
    return text if len(text) <= 160 else text[:157] + "..."


def conflict_cells(spec: FieldSpec, item: FieldResult, max_granularity: int) -> list[str]:
    """Columns of a conflicting list row whose stated values differ between groups."""
    cells = []
    for column in spec.items or []:
        values = [g.value.get(column.name) for g in item.groups]
        values = [v for v in values if v is not None]
        if any(
            not values_equal(column.type, values[0], v, max_granularity, rounding=column.rounds)
            for v in values[1:]
        ):
            cells.append(column.name)
    return cells


def open_decisions(
    draft: ProfileDraft, schema: ProfileSchema, max_granularity: int
) -> list[OpenDecision]:
    """Every decision the advisor must make before confirming, in schema order."""
    open_: list[OpenDecision] = []
    for spec in schema.fields:
        if spec.name not in draft.schema_fields:
            continue
        result = draft.field(spec.name)
        if not spec.is_list:
            if result.status in SCALAR_ACTIONS:
                open_.append(
                    OpenDecision(
                        field=spec.name,
                        kind=str(result.status),
                        allowed=SCALAR_ACTIONS[result.status],
                    )
                )
            continue
        for item in result.items:
            if item.status is FieldStatus.CONFLICT:
                open_.append(
                    OpenDecision(
                        field=spec.name,
                        key=item.key,
                        kind="row_conflict",
                        cells=conflict_cells(spec, item, max_granularity),
                        allowed=ROW_CONFLICT_ACTIONS,
                    )
                )
    return open_


def needs_review_labels(draft: ProfileDraft) -> list[str]:
    """Values shown prominently (OCR page, unverified evidence, Entwurf only)."""
    labels = []
    for f in draft.fields:
        if f.items:
            labels += [f"{f.name} {key_text(i.key)}" for i in f.items if i.needs_review]
        elif f.needs_review:
            labels.append(f.name)
    return labels


def typed_value_error(spec: FieldSpec, value: Any) -> str | None:
    """Same field-type rules as extraction; None when the typed value is valid."""
    if value is None:
        return "a value is required (use leave_empty for no value)"
    if spec.multiple:
        if not isinstance(value, list) or not value:
            return "expected a non-empty list"
        return next((e for v in value if (e := _scalar_error(spec, v))), None)
    return _scalar_error(spec, value)


def _scalar_error(spec: FieldSpec, value: Any) -> str | None:
    match spec.type:
        case FieldType.STRING:
            if spec.format == "email":
                from app.mail.validate import address

                try:
                    address(value)
                except ValueError as exc:
                    return str(exc)
            ok = isinstance(value, str) and bool(value.strip())
            return None if ok else "expected text"
        case FieldType.INTEGER:
            ok = isinstance(value, int) and not isinstance(value, bool)
            return None if ok else "expected a whole number"
        case FieldType.NUMBER:
            ok = isinstance(value, int | float) and not isinstance(value, bool)
            return None if ok and math.isfinite(value) else "expected a number"
        case FieldType.BOOLEAN:
            return None if isinstance(value, bool) else "expected true or false"
        case FieldType.DATE:
            try:
                date.fromisoformat(value)
            except (TypeError, ValueError):
                return "expected an ISO date (YYYY-MM-DD)"
            return None
        case FieldType.ENUM:
            allowed = spec.enum or []
            return None if value in allowed else f"must be one of: {', '.join(allowed)}"
    return f"unsupported field type {spec.type}"


def apply_decisions(
    draft: ProfileDraft, schema: ProfileSchema, request: ConfirmRequest, max_granularity: int
) -> tuple[ProfileDraft, ConfirmResult]:
    """Apply the advisor's decisions as overrides. Raises DecisionError with all problems."""
    confirmed = draft.model_copy(deep=True)
    result = ConfirmResult(
        company_id=draft.company_id,
        confirmed_by=request.confirmed_by,
        draft_version=request.draft_version,
    )
    errors: list[FieldError] = []
    specs = {s.name: s for s in schema.fields if s.name in draft.schema_fields}
    decided: set[tuple[str, str | None]] = set()  # (field, key text) with a decision

    for decision in request.decisions:
        spec = specs.get(decision.field)
        if spec is None:
            errors.append(_error(decision, "unknown field"))
            continue
        target = (decision.field, key_text(decision.key) if spec.is_list else None)
        if target in decided:
            errors.append(_error(decision, "more than one decision for this field"))
            continue
        decided.add(target)
        field = confirmed.field(spec.name)
        if spec.is_list:
            errors += _apply_row(spec, field, decision, result, max_granularity)
        else:
            errors += _apply_scalar(spec, field, decision, result)

    for open_ in open_decisions(draft, schema, max_granularity):
        target = (open_.field, key_text(open_.key) if open_.key is not None else None)
        if target not in decided:
            message = f"decision missing ({open_.kind})"
            errors.append(FieldError(field=open_.field, key=open_.key, message=message))
    if errors:
        raise DecisionError(errors)

    for spec in specs.values():  # accepted without a decision
        field = confirmed.field(spec.name)
        if spec.is_list:
            result.fields_accepted += sum(i.decision is None for i in field.items)
        elif field.decision is None and field.status is not FieldStatus.MISSING:
            result.fields_accepted += 1
    return confirmed, result


def _apply_scalar(
    spec: FieldSpec, field: FieldResult, decision: Decision, result: ConfirmResult
) -> list[FieldError]:
    if decision.key is not None:
        return [_error(decision, "a scalar field takes no key")]
    action = decision.action
    if action not in SCALAR_ACTIONS[FieldStatus.CONFLICT]:
        return [_error(decision, f"action {action} is not allowed for a scalar field")]
    if action is DecisionAction.CHOOSE:
        if decision.group is None or not 0 <= decision.group < len(field.groups):
            return [_error(decision, f"group must be 0 to {len(field.groups) - 1}")]
        group = field.groups[decision.group]
        field.decision = AppliedDecision(
            action=action,
            value=group.value,
            group=decision.group,
            text=f"Gewählt: {short(group.value)} ({sources_text(group)})",
        )
    elif action is DecisionAction.VALUE:
        if message := typed_value_error(spec, decision.value):
            return [_error(decision, message)]
        field.decision = AppliedDecision(
            action=action,
            value=decision.value,
            typed=True,
            text=f"Vom Steuerberater eingegeben: {short(decision.value)}",
        )
        result.values_typed += 1
    else:
        if spec.required:
            return [_error(decision, "a required field cannot be left empty")]
        field.decision = AppliedDecision(action=action, text="Vom Steuerberater leer gelassen")
        result.fields_left_empty += 1
    if field.status is FieldStatus.CONFLICT:
        result.conflicts_resolved += 1
    return []


def _apply_row(
    spec: FieldSpec,
    field: FieldResult,
    decision: Decision,
    result: ConfirmResult,
    max_granularity: int,
) -> list[FieldError]:
    if decision.key is None:
        return [_error(decision, "a list field needs the row key")]
    wanted = key_text(decision.key)
    item = next((i for i in field.items if key_text(i.key) == wanted), None)
    if item is None:
        return [_error(decision, "no such row in the draft")]
    conflict = item.status is FieldStatus.CONFLICT
    action = decision.action
    if action not in ROW_ACTIONS:
        return [_error(decision, f"action {action} is not allowed for a list row")]
    if action is DecisionAction.ACCEPT:
        if conflict:
            return [_error(decision, "a conflicting row needs edit or drop")]
        item.decision = AppliedDecision(
            action=action, value=item.chosen, group=0, text="Zeile unverändert übernommen"
        )
    elif action is DecisionAction.DROP:
        item.decision = AppliedDecision(action=action, text="Zeile vom Steuerberater verworfen")
        result.rows_dropped += 1
    else:
        errors = _edit_row(spec, item, decision, result, max_granularity)
        if errors:
            return errors
    if conflict:
        result.conflicts_resolved += 1
    return []


def _edit_row(
    spec: FieldSpec,
    item: FieldResult,
    decision: Decision,
    result: ConfirmResult,
    max_granularity: int,
) -> list[FieldError]:
    """Choose or type single cells; every conflicting cell must be decided."""
    errors = []
    conflicting = (
        conflict_cells(spec, item, max_granularity) if item.status is FieldStatus.CONFLICT else []
    )
    row = _common_values(spec, item, conflicting)
    missing = [c for c in conflicting if c not in decision.cells]
    if missing:
        errors.append(_error(decision, f"decide the conflicting cells: {', '.join(missing)}"))
    columns = {c.name: c for c in spec.items or []}
    source_group, typed, parts = None, 0, []
    for name, cell in decision.cells.items():
        column = columns.get(name)
        if column is None:
            errors.append(_error(decision, f"unknown column {name}"))
            continue
        if (cell.group is None) == (cell.value is None):
            errors.append(_error(decision, f"{name}: choose a group or type a value"))
            continue
        if cell.group is not None:
            if not 0 <= cell.group < len(item.groups):
                errors.append(
                    _error(decision, f"{name}: group must be 0 to {len(item.groups) - 1}")
                )
                continue
            group = item.groups[cell.group]
            row[name] = group.value.get(name)
            source_group = cell.group if source_group is None else source_group
            parts.append(f"{name} = {short(row[name])} ({sources_text(group)})")
        else:
            if message := typed_value_error(column, cell.value):
                errors.append(_error(decision, f"{name}: {message}"))
                continue
            row[name] = cell.value
            typed += 1
            parts.append(f"{name} = {short(cell.value)} (eingegeben)")
    if errors:
        return errors
    if source_group is None and item.status is not FieldStatus.CONFLICT:
        source_group = 0  # typed corrections of an agreed row keep the row's sources
    item.decision = AppliedDecision(
        action=DecisionAction.EDIT,
        value=row,
        group=source_group,
        typed=typed > 0,
        text="Zeile bearbeitet: " + "; ".join(parts) if parts else "Zeile übernommen",
    )
    result.values_typed += typed
    return []


def _common_values(spec: FieldSpec, item: FieldResult, conflicting: list[str]) -> dict[str, Any]:
    """Row values the groups agree on (gaps filled); conflicting cells start empty."""
    if item.status is not FieldStatus.CONFLICT:
        return dict(item.value or {})
    row: dict[str, Any] = {}
    for column in spec.items or []:
        stated = [g.value.get(column.name) for g in item.groups]
        stated = [v for v in stated if v is not None]
        row[column.name] = stated[0] if stated and column.name not in conflicting else None
    return row


def _error(decision: Decision, message: str) -> FieldError:
    return FieldError(field=decision.field, key=decision.key, message=message)


def _error_text(error: FieldError) -> str:
    where = f"{error.field} {key_text(error.key)}" if error.key else error.field
    return f"{where}: {error.message}"
