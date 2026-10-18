"""One fixed template; sending uses only the explicitly confirmed final request."""

import asyncio
from typing import Literal

from app.config import Settings
from app.db.repository import Repository
from app.mail.provider import DeliveryUncertain, MailError, MailProvider, make_provider
from app.mail.validate import address, mail_text
from app.models.outreach import EmailPreview, EmailTemplate, Outreach, SendRequest
from app.services.alerts import require_sendable


class SendError(MailError):
    """A send attempt exists; `outreach_id` names its durable record."""

    def __init__(self, message: str, outreach_id: int):
        super().__init__(message)
        self.outreach_id = outreach_id


class SendFailed(SendError):
    """The provider definitely rejected the message; the advisor may send again."""


class SendUncertain(SendError):
    """Acceptance unknown: the reservation stays pending until resolved deliberately."""


async def get_template(repository: Repository) -> EmailTemplate:
    return await repository.get_template()


async def edit_template(
    repository: Repository, settings: Settings, subject: str, body: str
) -> EmailTemplate:
    mail_text(subject, body, settings)
    return await repository.edit_template(subject, body)


async def prepare_email(repository: Repository, settings: Settings, alert_id: int) -> EmailPreview:
    alert = await repository.get_alert(alert_id)
    ui_history = [
        o
        for o in await repository.list_outreach(alert.company_id)
        if o.alert_id is None and (o.status == "pending" or alert.law.id in o.law_ids)
    ]
    require_sendable(
        alert,
        alert.outreach + ui_history,
        await repository.get_project_state(alert.company_id),
        await repository.company_busy(alert.company_id, settings.step_lock_timeout_s),
    )
    template = await repository.get_template()
    recipient = await repository.get_contact_email(alert.company_id) or ""
    from app.ui.templates import Contact, Signer, fill

    info = await repository.get_client_info(alert.company_id)
    contact = Contact(info.contact_name or "", info.contact_salutation) if info else Contact()
    signer = Signer(settings.sender_name, settings.advisor_label, settings.sender_firm)
    return EmailPreview(
        alert_id=alert_id,
        subject=fill(template.subject, contact, signer),
        body=fill(template.body, contact, signer),
        recipient=recipient,
        recipient_source="profile" if recipient else "missing",
        message=None if recipient else "Keine E-Mail-Adresse im Profil gefunden",
        sender=settings.gmail_address,
        mail_mode=settings.mail_mode,
        redirect_active=bool(settings.mail_redirect_to),
        template_updated_at=template.updated_at,
    )


async def send_email(
    repository: Repository,
    settings: Settings,
    request: SendRequest,
    provider: MailProvider | None = None,
) -> Outreach:
    # Revalidate even constructed/copied Pydantic objects; confirmation is always required.
    request = SendRequest.model_validate(request.model_dump())
    address(request.recipient, settings.mail_recipient_max_chars)
    mail_text(request.subject, request.body, settings)
    destination = settings.mail_redirect_to or request.recipient
    address(destination, settings.mail_recipient_max_chars)
    delivery_subject = (
        f"[TEST an {request.recipient}] {request.subject}"
        if settings.mail_redirect_to
        else request.subject
    )
    # Always check SMTP config, even with an injected test transport.
    configured = make_provider(settings)
    transport = provider if provider is not None else configured
    pending = await repository.reserve_outreach(request, destination, delivery_subject)
    try:
        message_id = await asyncio.to_thread(
            transport.send, destination, delivery_subject, request.body
        )
    except DeliveryUncertain:
        # Timeout/disconnect while submitting may mean the server accepted the message.
        raise SendUncertain(
            f"outreach {pending.id} pending: Vorheriger Versand unklar, "
            "bitte im Gmail-Gesendet-Ordner prüfen; use --outreach-resolve",
            pending.id,
        ) from None
    except MailError:
        await repository.finish_outreach(pending.id, "failed", error="mail provider rejected send")
        raise SendFailed(
            f"outreach {pending.id} failed; check mail configuration and retry", pending.id
        ) from None
    except Exception:
        # Unexpected transport failure is ambiguous too. Never expose its payload.
        raise SendUncertain(
            f"outreach {pending.id} pending: provider outcome unknown; use --outreach-resolve",
            pending.id,
        ) from None
    # A cancellation/crash/DB error here leaves the durable reservation pending.
    return await repository.finish_outreach(pending.id, "sent", message_id=message_id)


async def resolve_outreach(
    repository: Repository, outreach_id: int, as_status: Literal["sent", "failed"]
) -> Outreach:
    return await repository.finish_outreach(
        outreach_id,
        as_status,
        error="manually resolved as failed" if as_status == "failed" else None,
    )
