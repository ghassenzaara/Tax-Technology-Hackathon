"""Step 8 contract: what the advisor reviews and the decisions the Confirm call sends.

`ConfirmRequest` is the input of the confirm operation and the JSON format of the CLI's
decisions file. In UI terms (UI.md): Confirm = "the advisor's final choice per field". The
backend never pre-selects a suggestion: every open decision must be sent explicitly.

Example decisions file:

    {"company_id": "Hafenkante-extract",
     "draft_version": "3f2a...",           # from the review, refused when stale
     "confirmed_by": "Dr. Maria Muster",   # plain name until login exists
     "decisions": [
       {"field": "kaufpreis_eur", "action": "choose", "group": 1},
       {"field": "dd_report_am", "action": "value", "value": "2026-01-20"},
       {"field": "verkaeufer", "action": "leave_empty"},
       {"field": "steuerattribut", "key": {"art": "kst_verlustvortrag", "stichtag": "2024-12-31"},
        "action": "edit", "cells": {"betrag_eur": {"group": 0}}},
       {"field": "darlehen", "key": {"darlehensgeber": "X", "beginn_am": "2017-07-01"},
        "action": "drop"}]}
"""

from typing import Any

from pydantic import BaseModel, Field

from app.models.profile import DecisionAction, ProfileDraft
from app.models.project import ProjectState


class CellChoice(BaseModel):
    """One cell of a list row: take it from a candidate group, or type it. Exactly one."""

    group: int | None = None  # index into the row's `groups` (0-based, as in the review)
    value: Any = None  # typed value: numbers as numbers, dates as ISO strings


class Decision(BaseModel):
    """The advisor's choice for one scalar field or one list row."""

    field: str  # field name of the profile schema
    key: dict[str, Any] | None = None  # list rows only: the row's key exactly as reviewed
    action: DecisionAction
    group: int | None = None  # choose: index into the field's `groups` (0-based)
    value: Any = None  # value: the typed value
    cells: dict[str, CellChoice] = {}  # edit: column name -> choice


class ConfirmRequest(BaseModel):
    company_id: str
    draft_version: str  # version shown by the review; a newer draft makes the request stale
    confirmed_by: str = Field(min_length=1)
    decisions: list[Decision] = []


class OpenDecision(BaseModel):
    """Something the advisor must decide before the profile can be confirmed."""

    field: str
    key: dict[str, Any] | None = None  # list rows only
    kind: str  # conflict | missing | row_conflict
    cells: list[str] = []  # row_conflict: the columns whose values differ
    allowed: list[DecisionAction]  # actions that resolve it


class HintRow(BaseModel):
    """An open `pruefhinweis` row of the company (profil schema)."""

    id: int
    art: str
    feld: str | None = None
    beschreibung: str


class ReviewView(BaseModel):
    """Everything the review screen reads; no extraction happens here."""

    company_id: str
    state: ProjectState | None
    draft_version: str
    draft: ProfileDraft
    open_decisions: list[OpenDecision]
    needs_review: list[str]  # fields shown prominently, no decision required
    # TODO: decide whether needs-review values require an explicit acknowledgement.
    open_hints: list[HintRow]


class FieldError(BaseModel):
    """A per-field problem the UI shows next to the field."""

    field: str
    key: dict[str, Any] | None = None
    message: str


class ConfirmResult(BaseModel):
    company_id: str
    confirmed_by: str
    draft_version: str
    fields_accepted: int = 0  # confirmed or single_source values accepted without a decision
    conflicts_resolved: int = 0
    values_typed: int = 0
    fields_left_empty: int = 0
    rows_dropped: int = 0
    hints_closed: int = 0
    hints_open: int = 0
    rows: dict[str, int] = {}  # profil table -> rows written
