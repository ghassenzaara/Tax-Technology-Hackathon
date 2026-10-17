"""Plan section 4b: the advisor updates a confirmed profile by hand ("Update entries").

`UpdateRequest` is the input of the update operation and the JSON format of the CLI's
changes file. It needs no documents and runs no extraction. There is no undo (decided): the
UI shows a summary and asks "are you sure" first; a wrong value is corrected by a new
update, which the audit log records. Example:

    {"company_id": "Hafenkante-extract",
     "profile_version": 1,                   # version the UI saw; refused when stale
     "changed_by": "Dr. Maria Muster",       # plain name until login exists
     "reason": "Darlehen zurückgezahlt",     # optional
     "changes": [
       {"field": "finanzamt", "kind": "edit", "value": "Finanzamt Bremen"},
       {"field": "merkmal", "key": {"merkmal_code": "E01"}, "kind": "edit",
        "cells": {"wert": "nein", "kurztext": "Darlehen zurückgezahlt"}},
       {"field": "darlehen", "row_id": 12, "kind": "edit", "cells": {"status": "zurueckgezahlt"}},
       {"field": "beteiligung", "row_id": 7, "kind": "end", "end_date": "2026-05-12"},
       {"field": "finding", "kind": "add", "cells": {"finding_code": "K-9", ...}},
       {"field": "nutzungsvertrag", "row_id": 4, "kind": "remove"}]}

List rows are identified by `row_id` (from the profile read) or by their `key` values.
"""

from enum import StrEnum
from typing import Any
from uuid import UUID

from pydantic import BaseModel, Field

from app.models.audit import AuditEntry
from app.models.project import ProjectState


class ChangeKind(StrEnum):
    EDIT = "edit"  # scalar value or cells of a list row
    ADD = "add"  # a new list row
    REMOVE = "remove"  # delete a list row that was a mistake
    END = "end"  # set the validity end (gueltig_bis) instead of deleting


class Change(BaseModel):
    field: str  # profile schema field name
    kind: ChangeKind
    row_id: int | None = None  # list rows: profil row id
    key: dict[str, Any] | None = None  # or: the row's key values
    value: Any = None  # scalar edit: new value (None empties an optional field)
    cells: dict[str, Any] = {}  # list edit/add: column -> new value
    end_date: str | None = None  # end: ISO date


class UpdateRequest(BaseModel):
    company_id: str
    profile_version: int  # version the advisor saw; a newer one makes the request stale
    changed_by: str = Field(min_length=1)
    reason: str | None = None
    changes: list[Change] = Field(min_length=1)


class UpdateResult(BaseModel):
    company_id: str
    update_id: UUID  # shared by the audit entries of this call
    profile_version: int  # the new version
    changes: int
    hints_closed: int = 0
    changed_features: list[str] = []  # feature refs whose sentence must be rewritten
    embeddings_refreshed: bool = False
    embeddings_stale: bool = False
    refresh_error: str | None = None
    screening: dict[str, Any] | None = None  # re-screening report (screen_after_update)
    screening_error: str | None = None


class DocumentCitation(BaseModel):
    """The `dokument` row a value cites: the advisor re-checks it in their own copy."""

    id: int
    dateiname: str | None = None
    titel: str
    klasse_code: str
    dokument_datum: str | None = None  # ISO date


class ProfileRow(BaseModel):
    """A row of the confirmed profile with its citation (never an evidence quote)."""

    id: int
    values: dict[str, Any]
    fundstelle: str | None = None  # file and page, or "Vom Steuerberater eingegeben ..."
    quelle: DocumentCitation | None = None  # None for advisor-entered values
    konfidenz: str | None = None
    bestaetigt_von: str | None = None
    bestaetigt_am: str | None = None


class ConfirmedProfile(BaseModel):
    """Read side after confirmation: values, citations, version and the audit log."""

    company_id: str
    state: ProjectState
    profile_version: int | None
    embeddings_stale: bool
    scalars: dict[str, Any]  # deal and target company fields
    scalar_rows: dict[str, ProfileRow]  # "deal" / "gesellschaft" with their citation
    lists: dict[str, list[ProfileRow]]  # list field -> rows
    audit: list[AuditEntry]  # newest first
