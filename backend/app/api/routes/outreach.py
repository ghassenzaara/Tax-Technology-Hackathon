"""Alerts, the email template, preview, confirmed send and outreach history (steps 16 to
18). Reads never mark an alert seen. Sending is synchronous and never a job: the durable
pending reservation prevents a second delivery; an uncertain delivery is resolved only
deliberately (CLI `--outreach-resolve`). No calendar."""

from typing import Annotated

from fastapi import APIRouter, Query

from app.api.deps import ServicesDep
from app.api.errors import ERROR_RESPONSES, ApiError, ErrorCode, ErrorDetail
from app.api.schemas import DismissBody, OutreachList, SendBody, TemplateBody
from app.models.alert import Alert, AlertList, AlertStatus
from app.models.outreach import EmailPreview, EmailTemplate, Outreach, SendRequest
from app.services import alerts, email

router = APIRouter(tags=["alerts and email"], responses=ERROR_RESPONSES)


def _validation(exc: ValueError, field: str) -> ApiError:
    return ApiError(
        422, ErrorCode.VALIDATION, "invalid request", [ErrorDetail(field=field, message=str(exc))]
    )


@router.get("/api/alerts", response_model=AlertList, summary="Alerts with status counts")
async def alert_list(
    services: ServicesDep,
    status: Annotated[list[AlertStatus] | None, Query()] = None,
    company_id: str | None = None,
    law_id: int | None = None,
) -> AlertList:
    """Default: new and seen alerts, new first then by score. `counts` covers every status
    of the filtered company/law. An alert with `match.is_current=false` waits for
    screening and cannot be sent."""
    return await alerts.list_alerts(services.repository, status, company_id, law_id)


@router.get("/api/alerts/{alert_id}", response_model=Alert, summary="One alert (stays unseen)")
async def alert(services: ServicesDep, alert_id: int) -> Alert:
    return await alerts.get_alert(services.repository, alert_id)


@router.post("/api/alerts/{alert_id}/seen", response_model=Alert, summary="Mark an alert seen")
async def seen(services: ServicesDep, alert_id: int) -> Alert:
    return await alerts.mark_seen(services.repository, alert_id)


@router.post("/api/alerts/{alert_id}/dismiss", response_model=Alert, summary="Dismiss an alert")
async def dismiss(services: ServicesDep, alert_id: int, body: DismissBody | None = None) -> Alert:
    return await alerts.dismiss(services.repository, alert_id, body.reason if body else None)


@router.get("/api/email-template", response_model=EmailTemplate, summary="The fixed template")
async def template(services: ServicesDep) -> EmailTemplate:
    return await email.get_template(services.repository)


@router.post(
    "/api/email-template", response_model=EmailTemplate, summary="Edit the template (audited)"
)
async def edit_template(services: ServicesDep, body: TemplateBody) -> EmailTemplate:
    """The explicit template edit; editing one email never changes the template."""
    try:
        return await email.edit_template(
            services.repository, services.settings, body.subject, body.body
        )
    except ValueError as exc:
        raise _validation(exc, "template") from None


@router.get(
    "/api/alerts/{alert_id}/email-preview",
    response_model=EmailPreview,
    summary="The template and the client's address for one alert",
)
async def preview(services: ServicesDep, alert_id: int) -> EmailPreview:
    """Refused when the alert cannot be sent (sent, pending, closed, not current, stale
    vectors or active work of the company)."""
    return await email.prepare_email(services.repository, services.settings, alert_id)


@router.post(
    "/api/alerts/{alert_id}/send",
    response_model=Outreach,
    summary="Send the confirmed email (exactly these values)",
)
async def send(services: ServicesDep, alert_id: int, body: SendBody) -> Outreach:
    """Needs `confirmed=true`. The send guard is checked again while the pending record is
    reserved. Errors: send_pending (unresolved earlier attempt or uncertain delivery,
    with outreach_id), already_sent, send_failed (502, a new send may follow),
    mail_unavailable (503)."""
    try:
        request = SendRequest(alert_id=alert_id, **body.model_dump())
        return await email.send_email(
            services.repository, services.settings, request, services.mail
        )
    except ValueError as exc:
        raise _validation(exc, "email") from None


@router.get("/api/outreach", response_model=OutreachList, summary="Sent-email history")
async def outreach(
    services: ServicesDep, company_id: str | None = None, alert_id: int | None = None
) -> OutreachList:
    """Snapshots of every send attempt (recipient, subject, body, status), newest first."""
    return OutreachList(outreach=await services.repository.list_outreach(company_id, alert_id))
