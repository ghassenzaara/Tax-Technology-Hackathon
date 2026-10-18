"""Template, confirmed send request and permanent delivery snapshots."""

from datetime import datetime
from typing import Literal

from pydantic import BaseModel, ConfigDict

from app.config import Settings


class EmailTemplate(BaseModel):
    id: Literal[1] = 1
    subject: str
    body: str
    updated_at: datetime


PLACEHOLDER_GREETING = "{Anrede}"  # filled from the client contact (salutation and name)
PLACEHOLDER_SIGNATURE = "{Signatur}"  # filled from the advisor (name, title, firm)
PLACEHOLDERS = (PLACEHOLDER_GREETING, PLACEHOLDER_SIGNATURE)

DEFAULT_SUBJECT = "Gesprächsangebot zu aktuellen regulatorischen Entwicklungen"
DEFAULT_BODY = (
    "{Anrede},\n\n"
    "wir verfolgen für unsere Mandanten laufend die aktuellen regulatorischen Entwicklungen. "
    "Dabei sind uns Änderungen aufgefallen, die für Ihr Unternehmen von Bedeutung sein "
    "könnten.\n\n"
    "Gerne besprechen wir mit Ihnen in einem persönlichen Gespräch, ob und wie Ihr "
    "Unternehmen betroffen ist. Bitte teilen Sie uns mit, wann ein Termin für Sie passt.\n\n"
    "Mit freundlichen Grüßen\n{Signatur}"
)


def default_template(settings: Settings, now: datetime) -> EmailTemplate:
    """The frontend's text with placeholders; stored only in a new database or on reset."""
    # TODO: advisor to review the text.
    return EmailTemplate(subject=DEFAULT_SUBJECT, body=DEFAULT_BODY, updated_at=now)


class EmailPreview(BaseModel):
    alert_id: int
    subject: str
    body: str
    recipient: str
    recipient_source: Literal["profile", "missing"]
    message: str | None = None
    sender: str
    mail_mode: Literal["log", "smtp"]
    redirect_active: bool
    template_updated_at: datetime


class SendRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    alert_id: int
    recipient: str
    subject: str
    body: str
    confirmed: Literal[True]
    template_updated_at: datetime | None = None


class Outreach(BaseModel):
    id: int = 0
    alert_id: int | None  # None: one email for several laws (UI send, see law_ids)
    law_ids: list[int] = []  # UI send: the laws the email covers
    company_id: str
    recipient: str
    delivered_to: str
    subject: str  # exact submitted subject
    delivery_subject: str  # includes the test prefix when redirecting
    sent_body: str
    status: Literal["pending", "sent", "failed"] = "pending"
    error: str | None = None
    provider_message_id: str | None = None
    created_at: datetime
    sent_at: datetime | None = None
    template_updated_at: datetime
