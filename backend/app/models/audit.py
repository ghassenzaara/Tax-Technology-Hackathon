"""Audit log entries (plan section 4b.4): one append-only trail for confirmations, profile
changes, sent emails and template edits. Values and references only, never document text."""

from datetime import datetime
from enum import StrEnum
from typing import Any
from uuid import UUID

from pydantic import BaseModel


class AuditKind(StrEnum):
    PROFILE_CONFIRMED = "profile_confirmed"  # step 8: draft version and counts
    PROFILE_CHANGED = "profile_changed"  # one per change of an update call
    EMAIL_SENT = "email_sent"  # step 18: reference to the outreach snapshot
    TEMPLATE_EDITED = "template_edited"  # steps 17 and 18: old and new template text
    # UI contract layer (/api/ui): the advisor's review of a match, client notes, documents
    # uploaded into a confirmed client.
    MATCH_REVIEW_CHANGED = "match_review_changed"  # row_key {law_id}: field, old and new value
    NOTE_CHANGED = "note_changed"  # old and new note text
    DOCUMENTS_ADDED = "documents_added"  # new_value {files, run_id}


class AuditEntry(BaseModel):
    """A row of `pipeline.audit_log`. `id` and `acted_at` are set by the database."""

    id: int | None = None
    kind: AuditKind
    company_id: str | None = None  # empty for template edits
    update_id: UUID | None = None  # shared by the entries of one update call
    acted_by: str  # TODO: real identity once login exists (typed names are not tamper-proof)
    acted_at: datetime | None = None
    table_name: str | None = None
    field: str | None = None
    row_id: int | None = None
    row_key: dict[str, Any] | None = None
    change_kind: str | None = None  # edit | add | remove | end
    old_value: Any = None
    new_value: Any = None
    reason: str | None = None
    profile_version: int | None = None
    outreach_id: int | None = None
    screened_at: datetime | None = None  # set by matching once the change was re-screened


class AuditFilter(BaseModel):
    """Read filters; every one is optional. Results are newest first."""

    company_id: str | None = None
    kind: AuditKind | None = None
    acted_by: str | None = None
    since: datetime | None = None
    until: datetime | None = None
    limit: int = 500
