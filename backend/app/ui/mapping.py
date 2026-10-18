"""Mapping between the backend's models and the frontend contract (app/ui/contract.py).

Pure functions, no I/O. Ids the UI sends back are built here and read here only:

- fact ids: a scalar field name (`kaufpreis_eur`); a Merkmal `merkmal:<code>`; a whole list
  of a confirmed profile `list:<field>`; one list row of a draft `row:<field>:<key json>`;
  a candidate `<fact id>#<group index>`;
- match ids `<law id>~<company id>` (one match per company and law change).

Lists become one fact of type `list` whose value is an array of rows keyed by German column
labels. Multi-value cells are joined with ", " and booleans shown as ja/nein, so the
frontend's table editor can edit every cell as text; values coming back are converted to
the column type here (`coerce`).
"""

import json
import math
import re
from datetime import date, datetime
from decimal import Decimal
from typing import Any

from app.models.audit import AuditEntry as BackendAuditEntry
from app.models.audit import AuditKind
from app.models.client import ClientInfo
from app.models.client import MatchReview as ReviewRow
from app.models.law import Law, LawKind
from app.models.law import LawSection as BackendSection
from app.models.match import Match as Decision
from app.models.outreach import Outreach as BackendOutreach
from app.models.profile import CandidateGroup, FieldResult, FieldStatus, ProfileDraft
from app.models.review import CellChoice, ReviewView
from app.models.review import Decision as ConfirmDecision
from app.models.run import Run, RunDocument, RunState
from app.models.update import ADVISOR_SOURCE, Change, ChangeKind, ConfirmedProfile, ProfileRow
from app.profile.review import key_text
from app.profile.schema import FieldSpec, FieldType, ProfileSchema
from app.ui import contract as ui
from app.ui.labels import (
    CLIENT_LABELS,
    CORE_FIELDS,
    TABLE_GROUPS,
    column_for_label,
    column_label,
    field_label,
)

SCALAR_TABLES = ("deal", "gesellschaft")
MERKMAL = "merkmal"
TRUE_TEXT, FALSE_TEXT = "ja", "nein"
REVIEW_REASON = (
    "Der Wert stammt von einer gescannten Seite, nur aus einem Entwurf oder sein Beleg wurde "
    "auf der genannten Seite nicht gefunden. Bitte in den eigenen Unterlagen prüfen."
)
_PAGE = re.compile(r"^(?P<file>.*?)\s*S\.\s*(?P<page>\d+)")


class MappingError(ValueError):
    """A value from the UI does not fit its field; the message is German and shown as is."""


# Small helpers -------------------------------------------------------------------------


def iso(value: datetime | date | str | None) -> str | None:
    if value is None:
        return None
    if isinstance(value, datetime):
        return value.isoformat().replace("+00:00", "Z")
    if isinstance(value, date):
        return value.isoformat()
    return str(value)


def _plain(value: Any) -> Any:
    if isinstance(value, Decimal):
        return int(value) if value == value.to_integral_value() else float(value)
    if isinstance(value, datetime | date):
        return value.isoformat()
    return value


def fact_type(spec: FieldSpec) -> str:
    return "list" if spec.multiple or spec.is_list else str(spec.type)


def display_cell(spec: FieldSpec, value: Any) -> Any:
    """A list cell as the table editor shows it."""
    value = _plain(value)
    if value is None:
        return None
    if isinstance(value, list):
        return ", ".join(str(_plain(v)) for v in value)
    if isinstance(value, bool):
        return TRUE_TEXT if value else FALSE_TEXT
    return value


def display_row(spec: FieldSpec, values: dict[str, Any]) -> dict[str, Any]:
    """One list row keyed by German column labels, every schema column present."""
    return {
        column_label(spec.name, item.name): display_cell(item, values.get(item.name))
        for item in spec.items or []
        if item.db or item.name in values
    }


def coerce(spec: FieldSpec, value: Any, label: str | None = None) -> Any:
    """A value from the UI as the field type needs it; MappingError when it does not fit."""
    where = label or field_label(spec.name)
    if value is None or (isinstance(value, str) and not value.strip()):
        return None
    if spec.multiple:
        parts = value if isinstance(value, list) else str(value).split(",")
        single = spec.model_copy(update={"multiple": False})
        values = [coerce(single, p, label) for p in parts]
        return [v for v in values if v is not None] or None
    text = value.strip() if isinstance(value, str) else value
    try:
        match spec.type:
            case FieldType.INTEGER:
                if isinstance(text, bool):
                    raise ValueError
                number = float(str(text).replace(",", ".")) if isinstance(text, str) else text
                if number != int(number):
                    raise ValueError
                return int(number)
            case FieldType.NUMBER:
                if isinstance(text, bool):
                    raise ValueError
                number = float(str(text).replace(",", ".")) if isinstance(text, str) else text
                if not math.isfinite(number):
                    raise ValueError
                return int(number) if float(number).is_integer() else float(number)
            case FieldType.BOOLEAN:
                if isinstance(text, bool):
                    return text
                lowered = str(text).lower()
                if lowered in (TRUE_TEXT, "true", "1", "wahr"):
                    return True
                if lowered in (FALSE_TEXT, "false", "0", "falsch"):
                    return False
                raise ValueError
            case FieldType.DATE:
                return date.fromisoformat(str(text)).isoformat()
            case FieldType.ENUM:
                if str(text) not in (spec.enum or []):
                    allowed = ", ".join(spec.enum or [])
                    raise MappingError(f"{where}: erlaubt sind {allowed}.")
                return str(text)
            case _:
                return str(text)
    except MappingError:
        raise
    except (TypeError, ValueError):
        expected = {
            FieldType.INTEGER: "eine ganze Zahl",
            FieldType.NUMBER: "eine Zahl",
            FieldType.BOOLEAN: "ja oder nein",
            FieldType.DATE: "ein Datum (JJJJ-MM-TT)",
        }.get(spec.type, "ein gültiger Wert")
        raise MappingError(f"{where}: bitte {expected} eingeben.") from None


def parse_citation(fundstelle: str | None, filename: str | None) -> ui.Source | None:
    """`fundstelle` is "S. 3" (with the cited document) or "file.pdf S. 3"."""
    if not fundstelle or fundstelle.startswith(ADVISOR_SOURCE):
        return None
    found = _PAGE.match(fundstelle)
    if not found:
        return None
    name = filename or found.group("file").strip()
    if not name:
        return None
    return ui.Source(filename=name, page=max(int(found.group("page")), 1))


def row_source(row: ProfileRow) -> ui.Source | None:
    return parse_citation(row.fundstelle, row.quelle.dateiname if row.quelle else None)


def advisor_row(row: ProfileRow) -> bool:
    return bool(row.fundstelle and row.fundstelle.startswith(ADVISOR_SOURCE))


def _unique(sources: list[ui.Source]) -> list[ui.Source]:
    seen: dict[tuple, ui.Source] = {}
    for s in sources:
        seen.setdefault((s.filename, s.page, s.quote), s)
    return list(seen.values())


def group_sources(group: CandidateGroup, *, quotes: bool = True) -> list[ui.Source]:
    return _unique(
        [
            ui.Source(
                filename=c.source_filename,
                page=max(c.source_page, 1),
                quote=c.evidence if quotes else None,
            )
            for c in group.candidates
        ]
    )


def db_key(spec: FieldSpec, values: dict[str, Any]) -> str:
    """Row identity against the confirmed profile: the key columns stored in profil (link
    helpers like a finding's audit years are not columns there)."""
    return key_text({k: _plain(values.get(k)) for k in spec.key or [] if spec.item(k).db})


def matches_value(a: Any, b: Any) -> bool:
    return json.dumps(_plain(a), sort_keys=True, default=str) == json.dumps(
        _plain(b), sort_keys=True, default=str
    )


# Profile draft (step 8 review) ---------------------------------------------------------


def _alternatives(spec: FieldSpec, group: CandidateGroup) -> list[ui.ValueWithSources] | None:
    """Members of a rounding group whose value differs from the group's value."""
    if not group.rounding_equivalent:
        return None
    other = [c for c in group.candidates if not matches_value(c.value, group.value)]
    return [
        ui.ValueWithSources(
            value=c.value,
            sources=[ui.Source(filename=c.source_filename, page=c.source_page, quote=c.evidence)],
        )
        for c in other
    ] or None


def _scalar_draft_fact(spec: FieldSpec, result: FieldResult) -> ui.Fact:
    conflict = result.status is FieldStatus.CONFLICT
    fact = ui.Fact(
        id=spec.name,
        group=TABLE_GROUPS.get(spec.table or "", spec.table),
        label=field_label(spec.name),
        type=fact_type(spec),
        enum_values=list(spec.enum) if spec.enum else None,
        unit=spec.unit,
        code=spec.name,
        core_field=CORE_FIELDS.get(spec.name),
        review_status=str(result.status),
        needs_review=result.needs_review or None,
        review_reason=REVIEW_REASON if result.needs_review else None,
    )
    if conflict:
        # Never pre-selected: no value, every candidate with its sources (UI.md 8, 10).
        fact.candidates = [
            ui.Candidate(
                id=f"{spec.name}#{n}",
                value=g.value,
                sources=group_sources(g),
                alternatives=_alternatives(spec, g),
            )
            for n, g in enumerate(result.groups)
        ]
        if result.intra_document_conflict:
            fact.reasoning = "Ein Dokument nennt mehrere unterschiedliche Werte."
    elif result.groups:
        fact.value = result.value
        fact.sources = group_sources(result.groups[0])
        fact.alternatives = _alternatives(spec, result.groups[0])
    _reconciliation_view(fact, spec, result)
    return fact


def _reconciliation_view(fact: ui.Fact, spec: FieldSpec, result: FieldResult) -> None:
    if result.reconciliation is None:
        return
    fact.reasoning = f"KI-Abgleich: {result.reconciliation.reason}"
    fact.alternatives = [
        ui.ValueWithSources(
            value=[display_row(spec, g.value)] if spec.is_list else g.value,
            sources=group_sources(g),
        )
        for g in result.reconciliation.original_groups
    ]


def key_summary(spec: FieldSpec, key: dict[str, Any] | None, merkmal_names: dict[str, str]) -> str:
    key = key or {}
    if spec.name == MERKMAL and "merkmal_code" in key:
        return merkmal_names.get(key["merkmal_code"], key["merkmal_code"])
    parts = [display_cell(spec.item(k), key.get(k)) for k in spec.key or [] if key.get(k)]
    return ", ".join(str(p) for p in parts) or "neue Zeile"


def row_fact_id(spec: FieldSpec, key: dict[str, Any] | None) -> str:
    return f"row:{spec.name}:{key_text(key)}"


def _row_draft_fact(spec: FieldSpec, item: FieldResult, merkmal_names: dict[str, str]) -> ui.Fact:
    fact_id = row_fact_id(spec, item.key)
    fact = ui.Fact(
        id=fact_id,
        group=field_label(spec.name),
        label=f"{field_label(spec.name)}: {key_summary(spec, item.key, merkmal_names)}",
        type="list",
        code=spec.name,
        review_status=str(item.status),
        needs_review=item.needs_review or None,
        review_reason=REVIEW_REASON if item.needs_review else None,
    )
    if item.status is FieldStatus.CONFLICT:
        fact.candidates = [
            ui.Candidate(
                id=f"{fact_id}#{n}",
                value=[display_row(spec, g.value or {})],
                sources=group_sources(g),
            )
            for n, g in enumerate(item.groups)
        ]
    elif item.groups:
        fact.value = [display_row(spec, item.value or {})]
        fact.sources = group_sources(item.groups[0])
    _reconciliation_view(fact, spec, item)
    return fact


def draft_facts(
    draft: ProfileDraft, schema: ProfileSchema, merkmal_names: dict[str, str]
) -> list[ui.Fact]:
    """Every scalar field and every list row of the draft, in schema order."""
    facts = []
    for spec in schema.fields:
        if spec.name not in draft.schema_fields:
            continue
        result = draft.field(spec.name)
        if spec.is_list:
            facts += [_row_draft_fact(spec, i, merkmal_names) for i in result.items]
        else:
            facts.append(_scalar_draft_fact(spec, result))
    return facts


def update_draft_facts(
    draft: ProfileDraft,
    schema: ProfileSchema,
    profile: ConfirmedProfile,
    merkmal_names: dict[str, str],
) -> list[ui.Fact]:
    """New documents for a confirmed client: only what is new or differs from the profile
    (scalars by value, list rows by key). Nothing found changes nothing."""
    facts = []
    for spec in schema.fields:
        if spec.name not in draft.schema_fields:
            continue
        result = draft.field(spec.name)
        if not spec.is_list:
            if result.status is FieldStatus.MISSING:
                continue
            current = profile.scalars.get(spec.name)
            if result.status is not FieldStatus.CONFLICT and matches_value(result.value, current):
                continue
            fact = _scalar_draft_fact(spec, result)
            if current is not None:
                fact.reasoning = (
                    fact.reasoning + "\n" if fact.reasoning else ""
                ) + f"Bisher im Profil: {_short(current)}"
            facts.append(fact)
            continue
        existing = {db_key(spec, r.values): r for r in profile.lists.get(spec.name, [])}
        for item in result.items:
            old = existing.get(db_key(spec, item.key or {}))
            if old is not None and item.status is not FieldStatus.CONFLICT:
                stated = {k: v for k, v in (item.value or {}).items() if v is not None}
                if all(matches_value(v, old.values.get(k)) for k, v in stated.items()):
                    continue
            fact = _row_draft_fact(spec, item, merkmal_names)
            if old is not None:
                fact.reasoning = (
                    (fact.reasoning + "\n" if fact.reasoning else "")
                    + "Bisher im Profil: "
                    + _short(display_row(spec, old.values))
                )
            facts.append(fact)
    return facts


def _short(value: Any) -> str:
    if isinstance(value, dict):
        value = "; ".join(f"{k}: {v}" for k, v in value.items() if v not in (None, ""))
    text = value if isinstance(value, str) else json.dumps(_plain(value), ensure_ascii=False)
    return text if len(text) <= 300 else text[:297] + "..."


def _resolved_row(spec: FieldSpec, value: Any) -> dict[str, Any]:
    """The one row of a row fact's own value, converted to column types."""
    rows = value if isinstance(value, list) else [value]
    rows = [r for r in rows if isinstance(r, dict) and any(v not in (None, "") for v in r.values())]
    if len(rows) != 1:
        raise MappingError(f"{field_label(spec.name)}: bitte genau eine Zeile angeben.")
    columns = {i.name: i for i in spec.items or []}
    row = {}
    for label, cell in rows[0].items():
        column = column_for_label(spec.name, label)
        if column in columns:
            row[column] = coerce(columns[column], cell, f"{field_label(spec.name)}: {label}")
    return row


def decisions_from_resolutions(
    review: ReviewView, schema: ProfileSchema, resolutions: dict[str, ui.Resolution]
) -> list[ConfirmDecision]:
    """The advisor's choices per fact as confirm decisions. MappingError lists the facts
    that still need a decision (the backend decides what is open, not the UI)."""
    draft = review.draft
    specs = {s.name: s for s in schema.fields if s.name in draft.schema_fields}
    open_ids = {
        row_fact_id(specs[o.field], o.key) if o.key is not None else o.field
        for o in review.open_decisions
    }
    decisions: list[ConfirmDecision] = []
    missing: list[str] = []
    for spec in specs.values():
        result = draft.field(spec.name)
        if not spec.is_list:
            fact_id = spec.name
            resolution = resolutions.get(fact_id)
            decision = _scalar_decision(spec, result, resolution, fact_id in open_ids)
            if decision is not None:
                decisions.append(decision)
            elif fact_id in open_ids:
                missing.append(field_label(spec.name))
            continue
        for item in result.items:
            fact_id = row_fact_id(spec, item.key)
            resolution = resolutions.get(fact_id)
            decision = _row_decision(spec, item, resolution, fact_id)
            if decision is not None:
                decisions.append(decision)
            elif fact_id in open_ids:
                missing.append(f"{field_label(spec.name)} ({key_summary(spec, item.key, {})})")
    if missing:
        raise UnresolvedFields(missing)
    return decisions


class UnresolvedFields(MappingError):
    def __init__(self, labels: list[str]):
        self.labels = labels
        super().__init__(f"Noch {len(labels)} offene Felder: {', '.join(labels)}")


def _candidate_index(candidate_id: str | None, fact_id: str) -> int | None:
    if not candidate_id or not candidate_id.startswith(f"{fact_id}#"):
        return None
    try:
        return int(candidate_id.rsplit("#", 1)[1])
    except ValueError:
        return None


def _scalar_decision(
    spec: FieldSpec, result: FieldResult, resolution: ui.Resolution | None, is_open: bool
) -> ConfirmDecision | None:
    if resolution is None:
        return None
    if resolution.kind == "left_empty":
        if spec.required:
            raise MappingError(f"{field_label(spec.name)} darf nicht leer bleiben.")
        return ConfirmDecision(field=spec.name, action="leave_empty")
    index = _candidate_index(resolution.candidate_id, spec.name)
    if index is not None and 0 <= index < len(result.groups):
        if not is_open and index == 0:
            return None  # accepting the agreed value needs no decision
        return ConfirmDecision(field=spec.name, action="choose", group=index)
    value = coerce(spec, resolution.value)
    if value is None:
        raise MappingError(f"{field_label(spec.name)}: bitte einen Wert eingeben.")
    if not is_open and matches_value(value, result.value):
        return None  # accepted as extracted (for example a value marked for review)
    return ConfirmDecision(field=spec.name, action="value", value=value)


def _row_decision(
    spec: FieldSpec, item: FieldResult, resolution: ui.Resolution | None, fact_id: str
) -> ConfirmDecision | None:
    if resolution is None:
        return None
    if resolution.kind == "left_empty":
        return ConfirmDecision(field=spec.name, key=item.key, action="drop")
    columns = [i.name for i in spec.items or []]
    index = _candidate_index(resolution.candidate_id, fact_id)
    if index is not None and 0 <= index < len(item.groups):
        if item.status is not FieldStatus.CONFLICT and index == 0:
            return None
        cells = {c: CellChoice(group=index) for c in columns if c in item.groups[index].value}
        return ConfirmDecision(field=spec.name, key=item.key, action="edit", cells=cells)
    row = _resolved_row(spec, resolution.value)
    if item.status is not FieldStatus.CONFLICT:
        current = item.value or {}
        changed = {c: v for c, v in row.items() if not matches_value(v, current.get(c))}
        changed = {c: v for c, v in changed.items() if v is not None}
        if not changed:
            return None  # accepted as extracted
        cells = {c: CellChoice(value=v) for c, v in changed.items()}
        return ConfirmDecision(field=spec.name, key=item.key, action="edit", cells=cells)
    cells = {c: CellChoice(value=v) for c, v in row.items() if v is not None}
    return ConfirmDecision(field=spec.name, key=item.key, action="edit", cells=cells)


def update_changes_from_draft(
    draft: ProfileDraft,
    schema: ProfileSchema,
    profile: ConfirmedProfile,
    decisions: list[ConfirmDecision],
) -> list[Change]:
    """New documents for a confirmed client become one profile update: the values of the
    confirmed draft (decisions applied) that are new or differ, as edit or add changes."""
    changes: list[Change] = []
    for spec in schema.fields:
        if spec.name not in draft.schema_fields:
            continue
        result = draft.field(spec.name)
        if not spec.is_list:
            if result.dropped:
                continue
            value = result.chosen
            if value is None or matches_value(value, profile.scalars.get(spec.name)):
                continue
            changes.append(Change(field=spec.name, kind=ChangeKind.EDIT, value=value))
            continue
        existing = {db_key(spec, r.values): r for r in profile.lists.get(spec.name, [])}
        for item in result.items:
            if item.dropped or not item.chosen:
                continue
            row = {k: v for k, v in dict(item.chosen).items() if v is not None}
            old = existing.get(db_key(spec, row))
            if old is None:  # links (columns without db) are set only when a row is added
                changes.append(Change(field=spec.name, kind=ChangeKind.ADD, cells=row))
                continue
            cells = {
                k: v
                for k, v in row.items()
                if (spec.item(k).db or (spec.name == "finding" and k == "normen"))
                and k not in (spec.key or [])
                and not matches_value(v, _plain(old.values.get(k)))
            }
            if cells:
                changes.append(
                    Change(field=spec.name, kind=ChangeKind.EDIT, row_id=old.id, cells=cells)
                )
    return changes


# Confirmed profile -> Company ----------------------------------------------------------


def _advisor_fields(audit: list[BackendAuditEntry]) -> set[str]:
    """Scalar fields and Merkmale the advisor changed after confirmation (their document
    source no longer backs the value)."""
    changed = set()
    for e in audit:
        if e.kind is not AuditKind.PROFILE_CHANGED or not e.field:
            continue
        if e.field == MERKMAL and e.row_key and "merkmal_code" in e.row_key:
            changed.add(f"merkmal:{e.row_key['merkmal_code']}")
        elif e.table_name in SCALAR_TABLES:
            changed.add(e.field)
    return changed


def company_facts(
    schema: ProfileSchema,
    profile: ConfirmedProfile,
    info: ClientInfo | None,
    merkmal_names: dict[str, str],
    *,
    include_core: bool = False,
) -> list[ui.Fact]:
    """The confirmed profile as facts: filename and page only, never quotes or candidates.
    Core fields fill the company core instead."""
    advisor = _advisor_fields(profile.audit)
    facts: list[ui.Fact] = []
    for spec in schema.fields:
        if spec.is_list:
            if spec.name == MERKMAL:
                facts += _merkmal_facts(spec, profile.lists.get(spec.name, []), merkmal_names)
            elif profile.lists.get(spec.name):
                facts.append(_list_fact(spec, profile.lists[spec.name]))
            continue
        if (spec.name in CORE_FIELDS and not include_core) or spec.table not in SCALAR_TABLES:
            continue
        value = _plain(profile.scalars.get(spec.name))
        sources = []
        if spec.name in advisor and value is not None:
            status, sources = "advisor_entered", []
        elif value is None:
            status, sources = "missing", []
        else:
            status = "confirmed"
        facts.append(
            ui.Fact(
                id=spec.name,
                group=TABLE_GROUPS.get(spec.table or "", spec.table),
                label=field_label(spec.name),
                value=value,
                type=fact_type(spec),
                enum_values=list(spec.enum) if spec.enum else None,
                unit=spec.unit,
                code=spec.name,
                core_field=CORE_FIELDS.get(spec.name),
                sources=sources,
                review_status=status,
            )
        )
    return facts


def _merkmal_facts(
    spec: FieldSpec, rows: list[ProfileRow], merkmal_names: dict[str, str]
) -> list[ui.Fact]:
    wert = spec.item("wert")
    facts = []
    for row in rows:
        code = row.values.get("merkmal_code")
        source = row_source(row)
        status = "advisor_entered" if advisor_row(row) else "confirmed"
        if row.values.get("wert") == "unbekannt" and not source:
            status = "missing"
        facts.append(
            ui.Fact(
                id=f"merkmal:{code}",
                group=field_label(MERKMAL),
                label=merkmal_names.get(code, code),
                value=row.values.get("wert"),
                type="enum",
                enum_values=list(wert.enum or []),
                code=code,
                sources=[],
                review_status=status,
                reasoning=row.values.get("kurztext") or None,
            )
        )
    return facts


def _list_fact(spec: FieldSpec, rows: list[ProfileRow]) -> ui.Fact:
    entered = any(advisor_row(r) for r in rows)
    return ui.Fact(
        id=f"list:{spec.name}",
        group=field_label(spec.name),
        label=field_label(spec.name),
        value=[display_row(spec, r.values) for r in rows],
        type="list",
        code=spec.name,
        sources=[],
        review_status="advisor_entered" if entered else "confirmed",
    )


def build_company(
    company_id: str,
    *,
    name: str,
    status: str,
    created_at: datetime | None,
    updated_at: datetime | None,
    confirmed_at: datetime | None = None,
    profile: ConfirmedProfile | None = None,
    schema: ProfileSchema | None = None,
    info: ClientInfo | None = None,
    merkmal_names: dict[str, str] | None = None,
) -> ui.Company:
    now = datetime.now().astimezone()
    scalars = profile.scalars if profile else {}
    info = info or ClientInfo(company_id=company_id)
    return ui.Company(
        id=company_id,
        name=name,
        status=status,
        created_at=iso(created_at or now),
        updated_at=iso(updated_at or created_at or now),
        confirmed_at=iso(confirmed_at),
        confirmed_by=ui.ADVISOR_ID if confirmed_at else None,
        last_dd_at=iso(scalars.get("dd_report_am")) if scalars.get("dd_report_am") else None,
        contact=ui.Contact(
            salutation=info.contact_salutation or None,
            name=info.contact_name or "",
            email=scalars.get("ansprechpartner_email") or None,
            phone=info.contact_phone or None,
        ),
        customer_number=info.customer_number or None,
        client_since=info.client_since or None,
        legal_form=scalars.get("rechtsform") or None,
        industry=scalars.get("wz_bezeichnung") or None,
        facts=company_facts(schema, profile, info, merkmal_names or {}) if profile else [],
        extra={"projekt": company_id} if profile else {},
        notes=info.notes or "",
    )


# Company update -> profile update ------------------------------------------------------


def profile_changes(
    schema: ProfileSchema, profile: ConfirmedProfile, facts: dict[str, Any], core: dict[str, Any]
) -> list[Change]:
    """Profile changes of an updateCompany call (client data is handled separately).
    Unchanged values produce no change."""
    specs = {s.name: s for s in schema.fields}
    changes: list[Change] = []
    wanted = dict(facts)
    for field, core_key in CORE_FIELDS.items():
        if core_key in core and field in specs:
            wanted[field] = core[core_key]
    for fact_id, value in wanted.items():
        if fact_id.startswith("merkmal:"):
            code = fact_id.split(":", 1)[1]
            spec = specs[MERKMAL]
            new = coerce(spec.item("wert"), value, "Merkmal")
            old = next(
                (r for r in profile.lists.get(MERKMAL, []) if r.values.get("merkmal_code") == code),
                None,
            )
            if old is None:
                raise MappingError("Merkmal wurde nicht gefunden.")
            if new is None:
                raise MappingError("Ein Merkmal braucht ja, nein oder unbekannt.")
            if not matches_value(new, old.values.get("wert")):
                changes.append(
                    Change(field=MERKMAL, kind=ChangeKind.EDIT, row_id=old.id, cells={"wert": new})
                )
        elif fact_id.startswith("list:"):
            spec = specs.get(fact_id.split(":", 1)[1])
            if spec is None or not spec.is_list:
                raise MappingError("Feld wurde nicht gefunden.")
            changes += _row_diff(spec, profile.lists.get(spec.name, []), value)
        else:
            spec = specs.get(fact_id)
            if spec is None or spec.is_list or spec.table not in SCALAR_TABLES:
                raise MappingError("Feld wurde nicht gefunden.")
            new = coerce(spec, value)
            if new is None and spec.required:
                raise MappingError(f"{field_label(spec.name)} darf nicht leer sein.")
            if not matches_value(new, _plain(profile.scalars.get(spec.name))):
                changes.append(Change(field=spec.name, kind=ChangeKind.EDIT, value=new))
    return changes


def _row_diff(spec: FieldSpec, old_rows: list[ProfileRow], value: Any) -> list[Change]:
    """A list edited as a whole table: rows by key (new key -> add, missing key -> remove,
    same key -> edit of the changed cells)."""
    if not isinstance(value, list):
        raise MappingError(f"{field_label(spec.name)}: erwartet wird eine Tabelle.")
    columns = {i.name: i for i in spec.items or []}
    keys = spec.key or []

    def row_key(values: dict[str, Any]) -> str:
        return db_key(spec, values)

    old = {row_key(r.values): r for r in old_rows}
    seen: set[str] = set()
    changes: list[Change] = []
    for raw in value:
        if not isinstance(raw, dict) or all(v in (None, "") for v in raw.values()):
            continue
        row = {}
        for label, cell in raw.items():
            column = column_for_label(spec.name, label)
            if column in columns:
                row[column] = coerce(columns[column], cell, f"{field_label(spec.name)}: {label}")
                if spec.name == "finding" and column == "normen" and row[column] is None:
                    row[column] = []
        key = row_key(row)
        if key in seen:
            raise MappingError(f"{field_label(spec.name)}: eine Zeile ist doppelt vorhanden.")
        seen.add(key)
        existing = old.get(key)
        if existing is None:
            cells = {k: v for k, v in row.items() if v is not None}
            changes.append(Change(field=spec.name, kind=ChangeKind.ADD, cells=cells))
            continue
        cells = {
            k: v
            for k, v in row.items()
            if (columns[k].db or (spec.name == "finding" and k == "normen"))
            and k not in keys
            and not matches_value(v, _plain(existing.values.get(k)))
        }
        if cells:
            changes.append(
                Change(field=spec.name, kind=ChangeKind.EDIT, row_id=existing.id, cells=cells)
            )
    for key, existing in old.items():
        if key not in seen:
            changes.append(Change(field=spec.name, kind=ChangeKind.REMOVE, row_id=existing.id))
    return changes


# Laws, matches, outreach ----------------------------------------------------------------

COURTS = (
    ("BFH", "Bundesfinanzhof"),
    ("Bundesfinanzhof", "Bundesfinanzhof"),
    ("EuGH", "Europäischer Gerichtshof"),
    ("BVerfG", "Bundesverfassungsgericht"),
    ("FG ", "Finanzgericht"),
)


def source_type(law: Law) -> str:
    if law.kind is LawKind.OTHER:
        text = f"{law.title} {law.reference or ''}"
        if re.search(r"urteil|beschluss", text, re.IGNORECASE):
            return "urteil"
    return str(law.kind)


def source_name(law: Law) -> str:
    text = f"{law.title} {law.reference or ''}"
    for marker, name in COURTS:
        if marker in text:
            return name
    return {
        LawKind.BMF_SCHREIBEN: "Bundesministerium der Finanzen",
        LawKind.GESETZENTWURF: "Deutscher Bundestag",
        LawKind.GESETZ: "Bundesgesetzblatt",
        LawKind.VERORDNUNG: "Bundesgesetzblatt",
    }.get(law.kind, "Rechtsquelle")


def law_change(law: Law, sections: list[BackendSection]) -> ui.LawChange:
    norms = sorted({n for s in sections for n in s.norms})
    blocks = []
    if law.summary:
        blocks.append(
            ui.LawBlock(kind="summary", title="Zusammenfassung", text=law.summary, generated=True)
        )
    if law.change_summary:
        blocks.append(
            ui.LawBlock(
                kind="changes", title="Was ändert sich?", text=law.change_summary, generated=True
            )
        )
    extra = {"Aktenzeichen / Fundstelle": law.reference} if law.reference else None
    return ui.LawChange(
        id=str(law.id),
        title=law.title,
        source_type=source_type(law),
        source_name=source_name(law),
        published_at=iso(law.published_at or (law.ingested_at or datetime.now()).date()),
        effective_at=iso(law.effective_at),
        detected_at=iso(law.ingested_at or datetime.now().astimezone()),
        content_hash=law.content_hash,
        tags=ui.LawTags(
            tax_types=list(law.tax_types) or None,
            legal_forms=list(law.legal_forms) or None,
            norms=norms or None,
        ),
        sections=[
            ui.LawSection(id=str(s.id), ref=s.section_ref, heading=s.heading, text=s.text)
            for s in sections
        ],
        blocks=blocks,
        extra=extra,
    )


def match_id(law_id: int, company_id: str) -> str:
    return f"{law_id}~{company_id}"


def parse_match_id(value: str) -> tuple[int, str]:
    law, _, company = value.partition("~")
    try:
        return int(law), company
    except ValueError:
        raise MappingError("Treffer wurde nicht gefunden.") from None


def feature_fact_id(decision: Decision, merkmal_codes: dict[int, str]) -> str | None:
    if decision.objekt_typ == "merkmal_wert" or decision.objekt_typ == MERKMAL:
        code = merkmal_codes.get(decision.objekt_id)
        return f"merkmal:{code}" if code else None
    return {"finding": "list:finding", "rechtsbehelf": "list:rechtsbehelf"}.get(decision.objekt_typ)


def build_match(
    company_id: str,
    law_id: int,
    decisions: list[Decision],
    review: ReviewRow | None,
    merkmal_codes: dict[int, str],
) -> ui.Match:
    """One match per company and law change: the assessment from the best relevant decision
    (current ones first), the review from the advisor's row."""
    hits = [d for d in decisions if d.judge_relevant]
    current = [d for d in hits if d.is_current]
    ranked = sorted(current or hits, key=lambda d: -d.score)
    best = ranked[0] if ranked else None
    review = review or ReviewRow(company_id=company_id, law_id=law_id)
    assessment = ui.MatchAssessment(
        score=float(best.score) if best else 0.0,
        confidence=float(best.judge_confidence or 0.0) if best else 0.0,
        reason=best.judge_reason if best else "",
        evidence=[
            ui.Evidence(
                fact_id=feature_fact_id(d, merkmal_codes),
                fact_label=d.feature_text,
                section_ref=d.section_ref or "",
                similarity=round(float(d.score), 4),
            )
            for d in ranked
        ],
        screened_at=iso(
            best.created_at if best and best.created_at else datetime.now().astimezone()
        ),
        run_id=f"screening-{best.id}" if best and best.id else "screening",
        current=bool(current),
        invalidation_reason=None if current or not best else str(best.invalidation_reason or ""),
        direction=str(best.judge_direction) if best and best.judge_direction else None,
    )
    return ui.Match(
        id=match_id(law_id, company_id),
        company_id=company_id,
        law_change_id=str(law_id),
        assessment=assessment,
        review=review_view(review),
    )


def review_view(review: ReviewRow) -> ui.MatchReview:
    return ui.MatchReview(
        label=review.label,
        email_status=review.email_status,
        notes=review.notes or None,
        sent_at=iso(review.sent_at),
        outreach_id=str(review.outreach_id) if review.outreach_id else None,
        discussed_at=iso(review.discussed_at),
        updated_by=ui.ADVISOR_ID if review.updated_at else None,
        updated_at=iso(review.updated_at),
        send_pending=True if review.pending_outreach_id else None,
    )


def outreach_view(row: BackendOutreach, law_of_alert: dict[int, int]) -> ui.Outreach:
    laws = row.law_ids or ([law_of_alert[row.alert_id]] if row.alert_id in law_of_alert else [])
    return ui.Outreach(
        id=str(row.id),
        company_id=row.company_id,
        match_ids=[match_id(law, row.company_id) for law in laws] or [f"alert-{row.alert_id}"],
        recipient=row.recipient,
        subject=row.subject,
        body=row.sent_body,
        sent_at=iso(row.sent_at or row.created_at),
        provider_message_id=row.provider_message_id,
    )


# Audit ---------------------------------------------------------------------------------


def audit_view(
    entry: BackendAuditEntry, schema: ProfileSchema, outreach: dict[int, BackendOutreach]
) -> ui.AuditEntry | None:
    """History entries of one company in the frontend's vocabulary (labels in German)."""
    if entry.company_id is None:
        return None
    kind, field, summary = str(entry.kind), entry.field, None
    old, new = entry.old_value, entry.new_value
    match_ref = None
    if entry.kind is AuditKind.PROFILE_CONFIRMED:
        summary, old, new = "Profil bestätigt", None, None
    elif entry.kind is AuditKind.PROFILE_CHANGED:
        field = _changed_label(entry, schema)
        summary = entry.reason or None
    elif entry.kind is AuditKind.EMAIL_SENT:
        sent = outreach.get(entry.outreach_id or 0)
        summary = f"E-Mail an {sent.recipient} gesendet" if sent else "E-Mail gesendet"
    elif entry.kind is AuditKind.MATCH_REVIEW_CHANGED:
        law_id = (entry.row_key or {}).get("law_id")
        match_ref = match_id(law_id, entry.company_id) if law_id is not None else None
    elif entry.kind is AuditKind.NOTE_CHANGED:
        field = "Notizen"
    elif entry.kind is AuditKind.DOCUMENTS_ADDED:
        files = (new or {}).get("files", 0) if isinstance(new, dict) else 0
        summary, old, new = f"{files} Dokumente hochgeladen", None, None
    return ui.AuditEntry(
        id=str(entry.id),
        company_id=entry.company_id,
        kind=kind,
        acted_at=iso(entry.acted_at or datetime.now().astimezone()),
        field=field,
        old_value=old,
        new_value=new,
        match_id=match_ref,
        outreach_id=str(entry.outreach_id) if entry.outreach_id else None,
        summary=summary,
    )


def _changed_label(entry: BackendAuditEntry, schema: ProfileSchema) -> str | None:
    if entry.field in CLIENT_LABELS:
        return CLIENT_LABELS[entry.field]
    if not entry.field:
        return None
    label = field_label(entry.field)
    if entry.row_key and entry.field != MERKMAL:
        spec = next((s for s in schema.fields if s.name == entry.field), None)
        if spec is not None and spec.is_list:
            return f"{label}: {key_summary(spec, entry.row_key, {})}"
    if entry.field == MERKMAL and entry.row_key:
        return f"{label}: {entry.row_key.get('merkmal_code')}"
    return label


# Upload runs ---------------------------------------------------------------------------

EXCEL = (".xlsx", ".xlsm")


def run_view(run: Run, documents: list[RunDocument], sizes: dict[str, int]) -> ui.UploadRun:
    status = {
        RunState.QUEUED: "running",
        RunState.RUNNING: "running",
        RunState.FINISHED: "finished",
    }.get(run.state, "failed")
    return ui.UploadRun(
        id=run.run_id,
        company_id=run.company_id,
        status=status,
        started_at=iso(run.started_at or run.created_at),
        finished_at=iso(run.finished_at) if status != "running" else None,
        documents=[_document_view(run, d, sizes.get(d.relative_path)) for d in documents],
        error=ui.ApiErrorBody(code="run_failed", message=run.last_error)
        if run.last_error
        else None,
    )


def _document_view(run: Run, d: RunDocument, size: int | None) -> ui.UploadDocument:
    lower = d.relative_path.lower()
    ocr = any("ocr" in w.lower() or "scan" in w.lower() for w in d.warnings)
    file_type = (
        ("scanned_pdf" if ocr else "text_pdf")
        if lower.endswith(".pdf")
        else "excel"
        if lower.endswith(EXCEL)
        else "unsupported"
    )
    error = None
    if d.parse_status == "failed" or d.extraction_status in ("failed", "write_failed"):
        status = d.extraction_status if d.parse_status != "failed" else "parse_failed"
        error = ui.ApiErrorBody(
            code=status, message=d.warnings[-1] if d.warnings else "Verarbeitung fehlgeschlagen"
        )
    return ui.UploadDocument(
        id=f"{run.run_id}:{d.relative_path}",
        relative_path=d.relative_path,
        size_bytes=size,
        file_type=file_type,
        parsing_status=d.parse_status,
        extraction_status=d.extraction_status,
        warnings=list(d.warnings),
        error=error,
    )
