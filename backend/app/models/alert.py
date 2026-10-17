"""Advisor alert view, joined to its match and legal source."""

from datetime import datetime
from enum import StrEnum

from pydantic import BaseModel

from app.models.law import Law
from app.models.match import Match
from app.models.outreach import Outreach


class AlertStatus(StrEnum):
    NEW = "new"
    SEEN = "seen"
    DISMISSED = "dismissed"
    ACTIONED = "actioned"
    OBSOLETE = "obsolete"


SENDABLE = frozenset({AlertStatus.NEW, AlertStatus.SEEN})


class Alert(BaseModel):
    id: int
    match_id: int | None
    company_id: str
    status: AlertStatus
    dismissed_reason: str | None = None
    created_at: datetime
    status_changed_at: datetime
    match: Match
    law: Law
    section_ref: str
    section_text: str
    page_from: int | None = None
    page_to: int | None = None
    recipient_available: bool = False
    outreach: list[Outreach] = []


class AlertList(BaseModel):
    alerts: list[Alert]
    counts: dict[AlertStatus, int]
