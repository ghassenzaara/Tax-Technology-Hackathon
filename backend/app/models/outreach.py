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


def default_template(settings: Settings, now: datetime) -> EmailTemplate:
    # TODO: advisor to review the proposed text, name and firm.
    return EmailTemplate(
        subject="Aktuelle Entwicklung im Steuerrecht – Austausch zu Ihrer Situation",
        body=(
            "Sehr geehrte Damen und Herren,\n\n"
            "im Rahmen unserer laufenden Beobachtung des Steuerrechts ist uns eine aktuelle "
            "Entwicklung aufgefallen, die für Ihr Unternehmen von Bedeutung sein kann. Gern "
            "ordnen wir diese für Sie ein und besprechen mögliche Risiken und "
            "Gestaltungsmöglichkeiten.\n\n"
            "Wir schlagen ein kurzes Telefonat in den nächsten Tagen vor. Antworten Sie uns "
            "einfach auf diese E-Mail oder rufen Sie uns an.\n\n"
            f"Mit freundlichen Grüßen\n{settings.sender_name}\n{settings.sender_firm}"
        ),
        updated_at=now,
    )


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
    alert_id: int
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
