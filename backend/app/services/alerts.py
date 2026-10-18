"""Read filters and explicit advisor actions. Reads never mark an alert seen."""

from app.db.repository import AlreadySentError, BusyError, Repository, SendPendingError, StateError
from app.models.alert import SENDABLE, Alert, AlertList, AlertStatus
from app.models.client import MatchReview
from app.models.outreach import Outreach
from app.models.project import ProjectState, ProjectStatus


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


def require_multi_sendable(
    company_id: str,
    reviews: dict[int, MatchReview | None],
    pending_outreach: int | None,
    current_laws: set[int],
    state: ProjectStatus | None,
    busy: str | None,
) -> None:
    """The send guard of one email covering several laws (the UI send). Checked under the
    review locks while the pending outreach is reserved."""
    if state is None or state.state not in {ProjectState.EMBEDDED, ProjectState.CLEANED}:
        raise StateError("Das Profil muss vor dem Versand bestätigt und verarbeitet sein.")
    if pending_outreach is not None:
        raise SendPendingError(
            f"Vorheriger Versand unklar, bitte im Gmail-Gesendet-Ordner prüfen "
            f"(outreach {pending_outreach}); use --outreach-resolve",
            pending_outreach,
        )
    for law_id, review in reviews.items():
        if review is None or review.label != "relevant":
            raise StateError(f"law {law_id} is not labeled relevant for '{company_id}'")
        if review.pending_outreach_id is not None:
            raise SendPendingError(
                f"the send of outreach {review.pending_outreach_id} is unclear; resolve it first",
                review.pending_outreach_id,
            )
        if review.email_status != "not_sent":
            if review.outreach_id is not None:
                raise AlreadySentError(
                    f"law {law_id} was sent to '{company_id}' already", review.outreach_id
                )
            raise StateError(f"law {law_id} is marked {review.email_status} for '{company_id}'")
        if law_id not in current_laws:
            raise StateError(
                f"law {law_id} has no current relevant decision for '{company_id}': screen again"
            )
    if state is not None and state.embeddings_stale:
        raise StateError(
            f"project '{company_id}' has stale vectors after an update; "
            "refresh and screen again first"
        )
    if busy:
        raise BusyError(f"project '{company_id}' is busy ({busy}); retry")


def validate_review_update(old: MatchReview | None, new: MatchReview) -> None:
    from app.db.repository import StatusLockedError

    if old is None:
        return
    if old.pending_outreach_id:
        raise SendPendingError(
            "Vorheriger Versand unklar; bitte zuerst auflösen.", old.pending_outreach_id
        )
    if old.outreach_id and (
        new.outreach_id != old.outreach_id
        or new.email_status
        not in ({"sent", "discussed"} if old.email_status == "sent" else {"discussed"})
    ):
        raise StatusLockedError("Der Versandstatus wurde geändert. Bitte neu laden.")


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
