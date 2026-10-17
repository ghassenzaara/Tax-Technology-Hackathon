"""Read filters and explicit advisor actions. Reads never mark an alert seen."""

from app.db.repository import AlreadySentError, BusyError, Repository, SendPendingError, StateError
from app.models.alert import SENDABLE, Alert, AlertList, AlertStatus
from app.models.outreach import Outreach
from app.models.project import ProjectStatus


def require_sendable(
    alert: Alert,
    history: list[Outreach],
    state: ProjectStatus | None = None,
    busy: str | None = None,
) -> None:
    """The send guard. Preview checks it; the durable reservation checks it again under the
    alert lock, so a decision invalidated meanwhile is never sent."""
    pending = next((o for o in history if o.status == "pending"), None)
    if pending:
        raise SendPendingError(
            f"Vorheriger Versand unklar, bitte im Gmail-Gesendet-Ordner prüfen "
            f"(outreach {pending.id}); use --outreach-resolve",
            pending.id,
        )
    sent = next((o for o in history if o.status == "sent"), None)
    if sent:
        raise AlreadySentError(f"alert {alert.id} already sent at {sent.sent_at}", sent.id)
    if alert.status not in SENDABLE:
        raise StateError(f"alert {alert.id} is {alert.status}; needs new or seen")
    if alert.match_id is None or not alert.match.is_current:
        reason = alert.match.invalidation_reason or "source replaced"
        raise StateError(
            f"alert {alert.id} is not current ({reason}): screen the company again first"
        )
    if state is not None and state.embeddings_stale:
        raise StateError(
            f"project '{alert.company_id}' has stale vectors after an update; "
            "refresh and screen again first"
        )
    if busy:
        raise BusyError(f"project '{alert.company_id}' is busy ({busy}); retry")


async def list_alerts(
    repository: Repository,
    statuses: list[AlertStatus] | None = None,
    company_id: str | None = None,
    law_id: int | None = None,
) -> AlertList:
    rows = await repository.list_alerts(company_id, law_id)
    selected = SENDABLE if statuses is None else set(statuses)
    counts = {s: sum(a.status == s for a in rows) for s in AlertStatus}
    rows = [a for a in rows if a.status in selected]
    rows.sort(key=lambda a: (a.status != AlertStatus.NEW, -a.match.score, a.created_at, a.id))
    return AlertList(alerts=rows, counts=counts)


async def get_alert(repository: Repository, alert_id: int) -> Alert:
    return await repository.get_alert(alert_id)


async def mark_seen(repository: Repository, alert_id: int) -> Alert:
    return await repository.change_alert_status(alert_id, AlertStatus.SEEN)


async def dismiss(repository: Repository, alert_id: int, reason: str | None = None) -> Alert:
    return await repository.change_alert_status(alert_id, AlertStatus.DISMISSED, reason)
