"""State the UI contract layer (/api/ui) adds next to the profile: client data, the
advisor's review of a match (one per company and law) and the read state of law changes.
Rows of `pipeline.client_info`, `screening.match_review` and `screening.law_seen`."""

from datetime import date, datetime
from typing import Any, Literal

from pydantic import BaseModel

MatchLabel = Literal["unlabeled", "relevant", "irrelevant"]
EmailStatus = Literal["not_sent", "sent", "discussed"]
ResponseOutcome = Literal["accepted", "rejected"]


class ClientInfo(BaseModel):
    """Client data that is not part of the extracted profile. Survives step 10."""

    company_id: str
    customer_number: str | None = None
    client_since: str | None = None
    notes: str | None = None
    contact_salutation: str | None = None
    contact_name: str | None = None
    contact_phone: str | None = None
    # Fact id -> {sources: [{filename, page}], status} of every confirmed scalar value,
    # written at confirmation (profil keeps one citation per row, not per field).
    citations: dict[str, dict[str, Any]] = {}
    updated_at: datetime | None = None


class MatchReview(BaseModel):
    """The advisor's review of one company and law change. The judge creates the match;
    the label and the email status are the advisor's and never change alerts."""

    company_id: str
    law_id: int
    label: MatchLabel = "unlabeled"
    email_status: EmailStatus = "not_sent"
    notes: str | None = None
    sent_at: datetime | None = None
    outreach_id: int | None = None  # set only by a send from the app
    discussed_at: datetime | None = None
    updated_at: datetime | None = None
    pending_outreach_id: int | None = None  # a reserved send whose outcome is not known yet


class MultiSendRequest(BaseModel):
    """One email to one client covering several law changes (the UI send). The backend
    sends exactly these values."""

    company_id: str
    law_ids: list[int]
    recipient: str
    subject: str
    body: str
    confirmed: Literal[True]


class OutreachResponse(BaseModel):
    """The client's reply to a sent email, recorded by hand (calendar). Accepted replies
    carry the date of the Besprechung. Row of `screening.outreach_response`."""

    outreach_id: int
    company_id: str
    outcome: ResponseOutcome
    received_on: date
    meeting_at: datetime | None = None  # set exactly when accepted
    note: str | None = None
    recorded_at: datetime | None = None
