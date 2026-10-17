"""Steps 16 to 18: offline delivery, durable reservations and database parity."""

import asyncio
import json
import os
import smtplib
import subprocess
import sys
import threading
from email.parser import BytesParser
from email.policy import SMTP, default

import pytest
from pydantic import ValidationError

from app import cli
from app.db.memory import MemoryRepository
from app.db.repository import StateError
from app.mail.provider import DeliveryUncertain, MailError, SMTPProvider
from app.models.alert import AlertStatus
from app.models.audit import AuditFilter, AuditKind
from app.models.match import Match
from app.models.outreach import SendRequest
from app.models.update import Change, UpdateRequest
from app.profile.merge import merge_candidates
from app.profile.review import typed_value_error
from app.profile.schema import build_response_model
from app.profile.update import check_request
from app.services import alerts, email
from app.services.confirm import confirm_profile
from app.services.embed import Embedder
from app.services.update import update_profile
from tests.helpers import FakeClient, entry, make_document
from tests.profil_data import cand, decide_all
from tests.test_confirm import published
from tests.test_extractor import make_extractor
from tests.test_screen import law, section


class FakeMail:
    def __init__(self, error=None):
        self.calls = []
        self.error = error

    def send(self, recipient, subject, body):
        self.calls.append((recipient, subject, body, threading.get_ident()))
        if self.error:
            raise self.error
        return "fake-message-id"


async def hit(repo, settings, project, nr=1, score=0.8):
    laws = await repo.list_laws()
    source = next((source for source in laws if source.content_hash == project), None)
    if source is None:
        law_id = await repo.save_law(
            law().model_copy(update={"content_hash": project}),
            [section(settings, n) for n in range(1, 5)],
            replace=False,
        )
    else:
        law_id = source.id
    sections = await repo.list_law_sections(law_id)
    match = Match(
        company_id=project,
        law_id=law_id,
        law_section_id=sections[nr - 1].id,
        objekt_typ="finding",
        objekt_id=nr,
        feature_text="Die Gesellschaft hat Verlustvorträge.",
        score=score,
        judge_relevant=True,
        judge_direction="risiko",
        judge_reason="Verlust gefährdet.",
        model="fake",
        profile_version=1,
    )
    await repo.save_matches([match])
    return next(
        a for a in await repo.list_alerts(project) if a.match.law_section_id == match.law_section_id
    )


@pytest.fixture
async def ready(settings, project):
    repo = MemoryRepository(settings)
    await repo.initialize()
    return repo, await hit(repo, settings, project)


def request(alert, **overrides):
    return SendRequest(
        **(
            {
                "alert_id": alert.id,
                "recipient": "client@example.test",
                "subject": "  Final subject  ",
                "body": "  Geänderter Text\nmit Leerzeichen.  ",
                "confirmed": True,
            }
            | overrides
        )
    )


async def test_success_exact_snapshot_redirect_and_audit(ready, settings):
    repo, alert = ready
    original = await email.get_template(repo)
    config = settings.model_copy(update={"mail_redirect_to": "self@example.test"})
    fake = FakeMail()
    req = request(alert)
    row = await email.send_email(repo, config, req, fake)
    assert row.status == "sent" and row.sent_at and row.provider_message_id == "fake-message-id"
    assert row.sent_body == req.body and row.subject == req.subject
    assert row.delivered_to == "self@example.test" and row.recipient == req.recipient
    assert fake.calls[0][:3] == (row.delivered_to, row.delivery_subject, req.body)
    assert row.delivery_subject == f"[TEST an {req.recipient}] {req.subject}"
    assert fake.calls[0][3] != threading.get_ident()  # SMTP never blocks the event loop
    assert (await repo.get_alert(alert.id)).status == AlertStatus.ACTIONED
    assert await email.get_template(repo) == original
    assert await repo.get_contact_email(alert.company_id) is None
    audit = await repo.list_audit(AuditFilter(kind=AuditKind.EMAIL_SENT))
    assert len(audit) == 1 and audit[0].outreach_id == row.id
    assert audit[0].new_value is None  # body lives only in the outreach snapshot
    with pytest.raises(StateError, match="already sent at"):
        await email.send_email(repo, config, req, fake)
    assert len(fake.calls) == 1


@pytest.mark.parametrize(
    "changes",
    [
        {"recipient": ""},
        {"recipient": "two@example.test,other@example.test"},
        {"recipient": "two@example.test;other@example.test"},
        {"recipient": "Name <a@example.test>"},
        {"recipient": "a@example.test\r\nBcc: other@example.test"},
        {"recipient": "a@localhost"},
        {"recipient": "a @example.test"},
        {"recipient": "a@-bad.test"},
        {"subject": ""},
        {"subject": "x\nBcc: x@example.test"},
        {"subject": "x\rheader"},
        {"subject": "x" * 201},
        {"body": "   "},
        {"body": "x" * 5001},
        {"body": "hello\x00world"},
        {"body": "invalid surrogate \ud800"},
    ],
)
async def test_invalid_send_writes_nothing(ready, settings, changes):
    repo, alert = ready
    fake = FakeMail()
    with pytest.raises(ValueError):
        await email.send_email(repo, settings, request(alert, **changes), fake)
    assert not await repo.list_outreach() and not repo.audit and not fake.calls
    assert (await repo.get_alert(alert.id)).status == AlertStatus.NEW


def test_confirmation_required():
    with pytest.raises(ValidationError):
        SendRequest(alert_id=1, recipient="a@example.test", subject="x", body="x", confirmed=False)
    with pytest.raises(ValidationError):
        SendRequest(alert_id=1, recipient="a@example.test", subject="x", body="x")


async def test_definite_failure_can_retry_and_error_is_sanitized(ready, settings):
    repo, alert = ready
    fake = FakeMail(MailError("password-secret and private body"))
    with pytest.raises(MailError, match="failed") as error:
        await email.send_email(repo, settings, request(alert), fake)
    assert "password-secret" not in str(error.value)
    (failed,) = await repo.list_outreach()
    assert failed.status == "failed" and "password-secret" not in failed.error
    assert (await repo.get_alert(alert.id)).status == AlertStatus.NEW and not repo.audit
    fake.error = None
    sent = await email.send_email(repo, settings, request(alert), fake)
    assert sent.status == "sent" and len(await repo.list_outreach()) == 2


@pytest.mark.parametrize("problem", [DeliveryUncertain("timeout"), TimeoutError("secret")])
@pytest.mark.parametrize("resolution", ["sent", "failed"])
async def test_ambiguous_delivery_blocks_until_manual_resolution(
    ready, settings, problem, resolution
):
    repo, alert = ready
    fake = FakeMail(problem)
    with pytest.raises(MailError, match="pending"):
        await email.send_email(repo, settings, request(alert), fake)
    (pending,) = await repo.list_outreach()
    assert pending.status == "pending" and not repo.audit
    with pytest.raises(StateError, match="Vorheriger Versand unklar"):
        await email.send_email(repo, settings, request(alert), fake)
    with pytest.raises(StateError, match="pending"):
        await alerts.dismiss(repo, alert.id)
    assert len(fake.calls) == 1
    resolved = await email.resolve_outreach(repo, pending.id, resolution)
    assert resolved.status == resolution
    if resolution == "sent":
        assert (await repo.get_alert(alert.id)).status == AlertStatus.ACTIONED
        assert len(repo.audit) == 1
    else:
        assert (await repo.get_alert(alert.id)).status == AlertStatus.NEW
        fake.error = None
        assert (await email.send_email(repo, settings, request(alert), fake)).status == "sent"
    with pytest.raises(StateError):
        await email.resolve_outreach(repo, pending.id, resolution)


async def test_crash_after_acceptance_preserves_reservation(ready, settings, monkeypatch):
    repo, alert = ready
    finish = repo.finish_outreach

    async def crash(*args, **kwargs):
        raise RuntimeError("database lost after provider accepted")

    monkeypatch.setattr(repo, "finish_outreach", crash)
    fake = FakeMail()
    with pytest.raises(RuntimeError, match="database lost"):
        await email.send_email(repo, settings, request(alert), fake)
    monkeypatch.setattr(repo, "finish_outreach", finish)
    with pytest.raises(StateError, match="Vorheriger Versand unklar"):
        await email.send_email(repo, settings, request(alert), fake)
    assert len(fake.calls) == 1 and not repo.audit


async def test_concurrent_send_one_provider_call(ready, settings):
    repo, alert = ready
    fake = FakeMail()
    results = await asyncio.gather(
        *(email.send_email(repo, settings, request(alert), fake) for _ in range(8)),
        return_exceptions=True,
    )
    assert len(fake.calls) == 1
    assert sum(isinstance(r, StateError) for r in results) == 7
    assert len(await repo.list_outreach()) == 1


async def test_alert_lifecycle_filter_counts_and_history(ready, settings, project):
    repo, first = ready
    second = await hit(repo, settings, project, 2, 0.9)
    await alerts.mark_seen(repo, second.id)
    third = await hit(repo, settings, project, 3, 0.95)
    await alerts.dismiss(repo, third.id, "Already discussed")
    listing = await alerts.list_alerts(repo, company_id=project, law_id=first.law.id)
    assert [a.id for a in listing.alerts] == [first.id, second.id]
    assert listing.counts[AlertStatus.DISMISSED] == 1
    assert (await alerts.get_alert(repo, first.id)).status == AlertStatus.NEW
    await repo.save_matches([first.match, second.match, third.match])
    assert len(await repo.list_alerts()) == 3
    negatives = [
        a.match.model_copy(update={"judge_relevant": False}) for a in (first, second, third)
    ]
    await repo.save_matches(negatives)
    assert (await repo.get_alert(first.id)).status == AlertStatus.OBSOLETE
    assert (await repo.get_alert(second.id)).status == AlertStatus.OBSOLETE
    assert (await repo.get_alert(third.id)).status == AlertStatus.DISMISSED
    assert not (await alerts.list_alerts(repo)).alerts
    await repo.save_matches([first.match, third.match])
    assert (await repo.get_alert(first.id)).status == AlertStatus.NEW
    assert (await repo.get_alert(third.id)).status == AlertStatus.DISMISSED
    with pytest.raises(StateError, match="dismissed"):
        await email.prepare_email(repo, settings, third.id)
    with pytest.raises(StateError, match="dismissed"):
        await alerts.mark_seen(repo, third.id)
    await email.send_email(repo, settings, request(first), FakeMail())
    await repo.save_matches([negatives[0]])
    assert (await repo.get_alert(first.id)).status == AlertStatus.ACTIONED
    assert (await alerts.list_alerts(repo, company_id="other")).counts[AlertStatus.NEW] == 0


async def test_preview_contact_and_fixed_template(ready, settings, profil_schema, project):
    repo, alert = ready
    missing = await email.prepare_email(repo, settings, alert.id)
    assert missing.recipient == "" and missing.recipient_source == "missing"
    assert missing.message == "Keine E-Mail-Adresse im Profil gefunden"
    repo.published[project] = merge_candidates(
        [cand(project, "ansprechpartner_email", "client@example.test", "report")],
        profil_schema,
        company_id=project,
        max_granularity=1000,
    )
    preview = await email.prepare_email(repo, settings, alert.id)
    assert preview.recipient == "client@example.test" and preview.recipient_source == "profile"
    assert preview.subject == missing.subject and preview.body == missing.body
    assert (await repo.get_alert(alert.id)).recipient_available
    assert alert.law.title not in preview.body and project not in preview.body


async def test_template_edit_once_and_log_provider(ready, settings):
    repo, alert = ready
    old = await email.get_template(repo)
    changed = await email.edit_template(repo, settings, "Neuer Betreff", "Neuer Text\n")
    await repo.initialize()
    assert await email.get_template(repo) == changed and changed.updated_at > old.updated_at
    edit = repo.audit[-1]
    assert edit.kind == AuditKind.TEMPLATE_EDITED and edit.company_id is None
    assert edit.old_value["body"] == old.body and edit.new_value["body"] == changed.body
    for subject, body in [("bad\nheader", "body"), ("x", " ")]:
        with pytest.raises(ValueError):
            await email.edit_template(repo, settings, subject, body)
    row = await email.send_email(repo, settings, request(alert))
    files = list((settings.data_dir / "mail").glob("*.json"))
    assert len(files) == 1
    logged = json.loads(files[0].read_text("utf-8"))
    assert logged["body"] == row.sent_body and logged["subject"] == row.subject
    assert not {"password", "sender", "gmail_address"} & logged.keys()


async def test_smtp_missing_credentials_writes_nothing(ready, settings):
    repo, alert = ready
    config = settings.model_copy(update={"mail_mode": "smtp", "gmail_address": ""})
    with pytest.raises(MailError, match="GMAIL_ADDRESS"):
        await email.send_email(repo, config, request(alert))
    config.gmail_address = "self@example.test"
    with pytest.raises(MailError, match="GMAIL_APP_PASSWORD"):
        await email.send_email(repo, config, request(alert))
    assert not await repo.list_outreach()


@pytest.mark.parametrize("failure", [None, "auth", "data", "timeout", "connect", "quit"])
@pytest.mark.parametrize(
    "body",
    [
        "Sehr geehrte Damen und Herren,\n",
        "Grüße ohne Schlusszeile",
        "Langer Text " * 100,
    ],
)
def test_smtp_tls_headers_utf8_and_sanitized_failures(settings, monkeypatch, failure, body):
    from pydantic import SecretStr

    from app.mail import provider

    calls = []

    class StubSMTP:
        def __init__(self, host, port, timeout):
            assert (host, port, timeout) == ("smtp.gmail.com", 587, settings.smtp_timeout_s)
            if failure == "connect":
                raise TimeoutError("secret")

        def ehlo(self):
            calls.append("ehlo")

        def starttls(self, context):
            assert context.check_hostname
            calls.append("tls")

        def login(self, username, password):
            assert username == "self@example.test" and password == "password-secret"
            calls.append("login")
            if failure == "auth":
                raise smtplib.SMTPAuthenticationError(535, b"password-secret")

        def send_message(self, message, from_addr, to_addrs):
            calls.append("send")
            assert message["To"] == "client@example.test" and message["Subject"] == "Grüße"
            assert message.get_content() == body
            wire = message.as_bytes(policy=SMTP)
            assert BytesParser(policy=default).parsebytes(wire).get_content() == body
            assert max(len(line) for line in wire.split(b"\r\n")) <= 998
            assert from_addr == "self@example.test" and to_addrs == ["client@example.test"]
            if failure == "timeout":
                raise TimeoutError("password-secret")
            if failure == "data":
                raise smtplib.SMTPDataError(554, b"private body password-secret")
            return {}

        def quit(self):
            if failure == "quit":
                raise OSError("socket closed after accepted mail")

        def close(self):
            pass

    monkeypatch.setattr(provider.smtplib, "SMTP", StubSMTP)
    config = settings.model_copy(
        update={
            "mail_mode": "smtp",
            "gmail_address": "self@example.test",
            "gmail_app_password": SecretStr("password-secret"),
            "sender_name": "Advisor",
        }
    )
    smtp = SMTPProvider(config)
    if failure in {"auth", "data", "timeout", "connect"}:
        with pytest.raises(DeliveryUncertain if failure == "timeout" else MailError) as err:
            smtp.send("client@example.test", "Grüße", body)
        assert "password-secret" not in str(err.value) and "private body" not in str(err.value)
    else:
        assert smtp.send("client@example.test", "Grüße", body)
        assert calls == ["ehlo", "tls", "ehlo", "login", "send"]


async def test_contact_extraction_scope_validation_and_empty(settings, profil_schema):
    payload = {s.name: [] if s.is_list else None for s in profil_schema.fields}
    payload["dokument"] = {"klasse_code": "dd_report", "titel": None, "dokument_datum": None}
    firm_only = "Verantwortliche Kanzlei: kanzlei@ro-stb.example. Kein Mandantenkontakt angegeben."
    client = FakeClient(text=json.dumps(payload))
    extracted = await make_extractor(profil_schema, settings, client).extract(
        make_document(pages=[firm_only]), "contact-test"
    )
    assert not extracted.candidates
    prompt = client.completions.requests[0]["messages"][0]["content"]
    assert "Never an advisor" in prompt and "Leave null if absent" in prompt
    model = build_response_model(profil_schema)
    bad = payload | {"ansprechpartner_email": entry("not an email", evidence="not an email")}
    with pytest.raises(ValidationError):
        model.model_validate(bad)
    assert model.model_validate(payload).ansprechpartner_email is None
    good = payload | {
        "ansprechpartner_email": entry("client@example.test", evidence="client@example.test")
    }
    client = FakeClient(text=json.dumps(good))
    extracted = await make_extractor(profil_schema, settings, client).extract(
        make_document(pages=["Mandantenkontakt: client@example.test"]), "contact-test"
    )
    assert extracted.candidates[0].value == "client@example.test"
    spec = profil_schema.field("ansprechpartner_email")
    assert typed_value_error(spec, "bad") and typed_value_error(spec, "client@example.test") is None
    for value in [None, "client@example.test"]:
        req = UpdateRequest(
            company_id="x",
            profile_version=1,
            changed_by="test",
            changes=[
                Change(field="ansprechpartner_email", kind="edit", value=value),
            ],
        )
        assert not check_request(req, profil_schema)


async def test_postgres_send_reservation_contact_update_and_history(
    pg_repository,
    settings,
    profil_schema,
    project,
):
    repo = pg_repository
    view = await published(repo, project, profil_schema, settings)
    await confirm_profile(repo, profil_schema, settings, decide_all(view))
    await Embedder(
        repo,
        profil_schema,
        settings,
        FakeClient(text="Die Gesellschaft hat steuerliche Verlustvorträge."),
    ).run(project)
    alert = await hit(repo, settings, project)
    assert (await email.prepare_email(repo, settings, alert.id)).recipient_source == "missing"
    for version, value in [(1, "client@example.test"), (2, None)]:
        result = await update_profile(
            repo,
            profil_schema,
            settings,
            UpdateRequest(
                company_id=project,
                profile_version=version,
                changed_by=settings.advisor_label,
                changes=[Change(field="ansprechpartner_email", kind="edit", value=value)],
            ),
            None,
        )
        assert not result.changed_features
        assert not result.embeddings_stale and not result.refresh_error
        assert await repo.get_contact_email(project) == value
        if value:
            snapshot = await repo.load_profile_snapshot(project, profil_schema)
            assert value not in snapshot.model_dump_json()
            assert value not in (await repo.company_features(project)).model_dump_json()
        audit = await repo.list_audit(
            AuditFilter(company_id=project, kind=AuditKind.PROFILE_CHANGED)
        )
        assert audit[0].field == "ansprechpartner_email" and audit[0].new_value == value
    fake = FakeMail()
    results = await asyncio.gather(
        *(email.send_email(repo, settings, request(alert), fake) for _ in range(5)),
        return_exceptions=True,
    )
    assert sum(isinstance(r, StateError) for r in results) == 4 and len(fake.calls) == 1
    (sent,) = await repo.list_outreach(project)
    assert sent.sent_body == request(alert).body and sent.status == "sent"
    assert (await repo.get_alert(alert.id)).status == AlertStatus.ACTIONED
    assert (
        len(await repo.list_audit(AuditFilter(company_id=project, kind=AuditKind.EMAIL_SENT))) == 1
    )
    second = await hit(repo, settings, project, 2)
    # A contact edit also preserves a stale marker left by an earlier profile edit.
    for version, field, value in [
        (3, "name", f"Updated {project} GmbH"),
        (4, "ansprechpartner_email", None),
    ]:
        result = await update_profile(
            repo,
            profil_schema,
            settings,
            UpdateRequest(
                company_id=project,
                profile_version=version,
                changed_by=settings.advisor_label,
                changes=[Change(field=field, kind="edit", value=value)],
            ),
            None,
            refresh=False,
        )
        assert result.embeddings_stale
        assert (await repo.get_project_state(project)).embeddings_stale
    # A non-contact edit invalidates the decisions until screening validates them again:
    # the open alert keeps its status but neither preview nor the reservation accepts it.
    (invalid,) = [
        m
        for m in await repo.list_matches(project, include_history=True)
        if m.law_section_id == second.match.law_section_id
    ]
    assert not invalid.is_current and invalid.invalidation_reason == "profile_changed"
    assert await repo.list_matches(project) == []
    with pytest.raises(StateError, match="not current"):
        await email.prepare_email(repo, settings, second.id)
    with pytest.raises(StateError, match="not current"):
        await email.send_email(repo, settings, request(second), FakeMail())
    assert (await repo.get_alert(second.id)).status == AlertStatus.NEW
    assert len(await repo.list_outreach(project)) == 1
    await repo.initialize()
    assert len(await repo.list_alerts(project)) == 2
    assert (await repo.get_alert(alert.id)).outreach[0] == sent
    # A replaced source keeps the sent snapshot and source context available for history.
    await repo.save_law(
        law().model_copy(update={"content_hash": project}),
        [section(settings)],
        replace=True,
    )
    assert (await repo.get_alert(alert.id)).status == AlertStatus.ACTIONED
    assert (await repo.list_outreach(project))[0] == sent


async def test_postgres_fail_resolve_alert_transitions_and_atomicity(
    pg_repository,
    settings,
    project,
):
    repo = pg_repository
    first = await hit(repo, settings, project)
    second = await hit(repo, settings, project, 2)
    third = await hit(repo, settings, project, 3)
    fourth = await hit(repo, settings, project, 4)
    await alerts.mark_seen(repo, second.id)
    await alerts.dismiss(repo, third.id, "Already handled")
    negatives = [
        a.match.model_copy(update={"judge_relevant": False}) for a in [first, second, third]
    ]
    await repo.save_matches(negatives)
    assert (await repo.get_alert(first.id)).status == AlertStatus.OBSOLETE
    assert (await repo.get_alert(second.id)).status == AlertStatus.OBSOLETE
    assert (await repo.get_alert(third.id)).status == AlertStatus.DISMISSED
    await repo.save_matches([first.match, second.match, third.match])
    assert (await repo.get_alert(first.id)).status == AlertStatus.NEW
    assert (await repo.get_alert(third.id)).status == AlertStatus.DISMISSED
    fake = FakeMail(MailError("rejected"))
    with pytest.raises(MailError):
        await email.send_email(repo, settings, request(first), fake)
    assert (await repo.list_outreach(project, first.id))[0].status == "failed"
    fake.error = DeliveryUncertain("timeout")
    with pytest.raises(MailError, match="pending"):
        await email.send_email(repo, settings, request(first), fake)
    (pending, _failed) = await repo.list_outreach(project, first.id)
    with pytest.raises(StateError, match="Vorheriger Versand unklar"):
        await email.send_email(repo, settings, request(first), fake)
    await email.resolve_outreach(repo, pending.id, "failed")
    fake.error = None
    assert (await email.send_email(repo, settings, request(first), fake)).status == "sent"
    other_pending = await repo.reserve_outreach(request(second), "client@example.test", "Subject")
    await email.resolve_outreach(repo, other_pending.id, "sent")
    assert (await repo.get_alert(second.id)).status == AlertStatus.ACTIONED
    await repo.save_matches(negatives[:2])
    assert all(
        [(await repo.get_alert(a.id)).status == AlertStatus.ACTIONED for a in [first, second]]
    )
    # A failing match/alert batch rolls back the earlier change in that same transaction.
    import psycopg

    with pytest.raises(psycopg.errors.ForeignKeyViolation):
        await repo.save_matches(
            [
                fourth.match.model_copy(update={"judge_relevant": False}),
                fourth.match.model_copy(update={"law_section_id": -1}),
            ]
        )
    assert (await repo.get_alert(fourth.id)).status == AlertStatus.NEW
    listing = await alerts.list_alerts(repo, company_id=project)
    assert [a.id for a in listing.alerts] == [fourth.id]
    assert listing.counts[AlertStatus.ACTIONED] == 2
    # Cleanup only removes extraction working data, preserving outreach and alerts.
    await repo.delete_working_data(project)
    assert len(await repo.list_alerts(project)) == 4
    assert (
        len(await repo.list_audit(AuditFilter(company_id=project, kind=AuditKind.EMAIL_SENT))) == 2
    )


def test_cli_log_send_template_and_outreach_flow(settings, project, monkeypatch, tmp_path, capsys):
    # A synchronous CLI entry point backed by the same persistent fake repository each call.
    config = settings.model_copy(update={"repository": "postgres"})
    repo = MemoryRepository(config)
    alert = asyncio.run(hit(repo, settings, project))
    monkeypatch.setattr(cli, "get_settings", lambda: config)
    monkeypatch.setattr(cli, "make_repository", lambda _: repo)
    assert cli.main(["--alerts", "--company-id", project]) == 0
    assert cli.main(["--alert-show", str(alert.id)]) == 0
    assert cli.main(["--email-preview", str(alert.id)]) == 0
    assert (asyncio.run(repo.get_alert(alert.id))).status == AlertStatus.NEW
    assert cli.main(["--alert-seen", str(alert.id)]) == 0
    template = tmp_path / "template.txt"
    template.write_text("Template subject\nTemplate body", "utf-8")
    assert cli.main(["--template-edit", str(template)]) == 0
    body = tmp_path / "body.txt"
    body.write_text("Explicit final email text\n", "utf-8")
    assert (
        cli.main(
            [
                "--email-send",
                str(alert.id),
                "--recipient",
                "typed@example.test",
                "--body-file",
                str(body),
                "--yes",
            ]
        )
        == 0
    )
    (sent,) = asyncio.run(repo.list_outreach())
    assert sent.sent_body == body.read_bytes().decode("utf-8")
    assert sent.subject == "Template subject"
    assert cli.main(["--outreach", "--company-id", project]) == 0
    assert (
        cli.main(["--email-send", str(alert.id), "--recipient", "typed@example.test", "--yes"]) == 1
    )
    assert "already sent at" in capsys.readouterr().err


def test_cli_confirmation_conflicts_and_no_calendar(settings, monkeypatch, capsys):
    config = settings.model_copy(update={"repository": "postgres"})
    monkeypatch.setattr(cli, "get_settings", lambda: config)
    assert cli.main(["--email-send", "1", "--recipient", "client@example.test"]) == 2
    assert "--yes" in capsys.readouterr().err
    assert cli.main(["--alerts", "--laws"]) == 2
    assert cli.main(["--outreach-resolve", "1"]) == 2
    assert "--as sent|failed" in capsys.readouterr().err
    with pytest.raises(SystemExit):
        cli._parse_args(["--calendar"])


async def test_postgres_cli_log_acceptance(pg_repository, settings, project, tmp_path):
    repo = pg_repository
    alert = await hit(repo, settings, project)
    environment = os.environ | {
        "DATABASE_URL": os.environ["TEST_DATABASE_URL"],
        "REPOSITORY": "postgres",
        "MAIL_MODE": "log",
        "DATA_DIR": str(tmp_path / "data"),
        "PYTHONIOENCODING": "utf-8",
    }

    async def run(*arguments):
        process = await asyncio.to_thread(
            subprocess.run,
            [sys.executable, "-m", "app.cli", *arguments],
            env=environment,
            capture_output=True,
            encoding="utf-8",
            timeout=30,
        )
        assert process.returncode == 0, process.stderr
        return process

    listing = tmp_path / "alerts.json"
    await run("--alerts", "--company-id", project, "--json", str(listing))
    assert json.loads(listing.read_text("utf-8"))["alerts"]["counts"]["new"] == 1
    preview_file = tmp_path / "preview.json"
    await run("--email-preview", str(alert.id), "--json", str(preview_file))
    preview = json.loads(preview_file.read_text("utf-8"))["email_preview"]
    assert preview["recipient_source"] == "missing" and preview["mail_mode"] == "log"
    body_file = tmp_path / "body.txt"
    body_file.write_bytes("Genau dieser bestätigte Text.\n".encode())
    await run(
        "--email-send",
        str(alert.id),
        "--recipient",
        "client@example.test",
        "--body-file",
        str(body_file),
        "--yes",
    )
    (sent,) = await repo.list_outreach(project)
    assert sent.sent_body == body_file.read_bytes().decode("utf-8")
    assert sent.status == "sent" and sent.provider_message_id.startswith("log-")
    assert (await repo.get_alert(alert.id)).status == AlertStatus.ACTIONED
    await run("--outreach", "--company-id", project)
