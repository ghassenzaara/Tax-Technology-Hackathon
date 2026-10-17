"""Findings 3 and 4: screening reconciles every earlier decision in scope and reuses a verdict
only while all judge inputs are unchanged (fingerprint). Memory repository and FakeClient;
the Postgres test (skipped without TEST_DATABASE_URL) covers parity and the migration."""

import os

import psycopg
import pytest

from app.db.repository import StateError
from app.models.alert import AlertStatus
from app.models.match import Invalidation, Match
from app.services import alerts, email
from app.services.confirm import confirm_profile
from app.services.embed import Embedder
from app.services.law_ingest import ingest_law
from app.services.screen import Screener
from tests.helpers import FakeClient
from tests.profil_data import decide_all
from tests.test_confirm import published
from tests.test_laws import METADATA, law_pdf
from tests.test_outreach import FakeMail, request
from tests.test_screen import feature, law, ready, section, verdict


def one_per_section(settings):
    """One candidate feature per section keeps fingerprints independent per section."""
    return settings.model_copy(update={"judge_features_per_section": 1})


async def screened(settings, project, **kwargs):
    repo = await ready(settings, project, **kwargs)
    client = FakeClient(text=verdict())
    screener = Screener(repo, settings, client)
    await screener.screen(project)
    return repo, client, screener


# Finding 3: reconciliation ---------------------------------------------------------------


async def test_removed_features_make_the_old_alert_obsolete_and_unsendable(settings, project):
    repo, client, screener = await screened(settings, project)
    (alert,) = await repo.list_alerts(project)
    assert alert.status is AlertStatus.NEW and alert.match.is_current

    repo.embeddings[project] = []  # every feature removed
    (report,) = await screener.screen(project)

    assert report.candidates == 0 and report.invalidated == 1 and client.completions.calls == 1
    assert await repo.list_matches(project) == []  # current decisions only
    (old,) = await repo.list_matches(project, include_history=True)
    assert old.judge_relevant and old.judge_reason  # the historical verdict is kept
    assert not old.is_current and old.invalidation_reason is Invalidation.NOT_CANDIDATE
    assert (await repo.get_alert(alert.id)).status is AlertStatus.OBSOLETE
    with pytest.raises(StateError):
        await email.prepare_email(repo, settings, alert.id)
    with pytest.raises(StateError):
        await email.send_email(repo, settings, request(alert), FakeMail())


@pytest.mark.parametrize("change", ["tax_type_rule", "below_threshold", "legal_form_rule"])
async def test_sections_that_no_longer_qualify_are_reconciled(settings, project, change):
    repo, client, screener = await screened(settings, project)
    (alert,) = await repo.list_alerts(project)
    stored = repo.embeddings[project][0]
    if change == "tax_type_rule":
        stored.steuerarten = ["USt"]
        next(iter(repo.sections.values())).tax_types = ["KSt"]
    elif change == "below_threshold":
        stored.vektor = [0.0, 1.0]  # cosine 0 against the section
    else:
        repo.laws[alert.law.id].legal_forms = ["GmbH"]
        repo.published[project] = _draft_with_form(project, "KG")

    (report,) = await screener.screen(project)

    assert report.invalidated == 1 and client.completions.calls == 1
    (old,) = await repo.list_matches(project, include_history=True)
    assert old.invalidation_reason is Invalidation.NOT_CANDIDATE
    assert (await repo.get_alert(alert.id)).status is AlertStatus.OBSOLETE


def _draft_with_form(project, form):
    from app.models.profile import FieldResult, FieldStatus, ProfileDraft

    return ProfileDraft(
        company_id=project,
        fields=[FieldResult(name="rechtsform", status=FieldStatus.SINGLE_SOURCE, value=form)],
        counts={},
        schema_fields=["rechtsform"],
    )


async def test_dismissed_and_actioned_history_stays(settings, project):
    settings = one_per_section(settings)
    repo, _, screener = await screened(
        settings,
        project,
        features=[feature(settings), feature(settings, 2, [0, 1])],
        sections=[section(settings), section(settings, 2, [0, 1])],
    )
    first, second = sorted(await repo.list_alerts(project), key=lambda a: a.match.objekt_id)
    await alerts.dismiss(repo, first.id, "erledigt")
    sent = await email.send_email(repo, settings, request(second), FakeMail())
    assert sent.status == "sent"

    repo.embeddings[project] = []
    (report,) = await screener.screen(project)

    assert report.invalidated == 2
    assert (await repo.get_alert(first.id)).status is AlertStatus.DISMISSED
    assert (await repo.get_alert(second.id)).status is AlertStatus.ACTIONED
    assert (await repo.list_outreach(project))[0].status == "sent"


async def test_law_scoped_screening_reconciles_only_its_law(settings, project):
    repo, client, screener = await screened(settings, project)
    other = await repo.save_law(
        law().model_copy(update={"content_hash": "second"}), [section(settings)], replace=False
    )
    await screener.screen(project, other)
    by_law = {a.law.id: a for a in await repo.list_alerts(project)}
    assert len(by_law) == 2

    repo.embeddings[project] = []
    (report,) = await screener.screen(project, other)

    assert report.invalidated == 1
    (current,) = await repo.list_matches(project)
    assert current.law_id != other
    assert (await repo.get_alert(by_law[other].id)).status is AlertStatus.OBSOLETE
    first = next(law_id for law_id in by_law if law_id != other)
    assert (await repo.get_alert(by_law[first].id)).status is AlertStatus.NEW


async def test_dry_run_changes_no_match_alert_or_marker(settings, project):
    repo, client, screener = await screened(settings, project)
    (alert,) = await repo.list_alerts(project)
    status = await repo.screening_status(project)
    repo.embeddings[project] = []

    (dry,) = await screener.screen(project, dry_run=True)

    assert dry.candidates == 0 and dry.invalidated == 0
    (match,) = await repo.list_matches(project)
    assert match.is_current and (await repo.get_alert(alert.id)).status is AlertStatus.NEW
    assert await repo.screening_status(project) == status
    assert client.completions.calls == 1


# Finding 4: fingerprints, budget and failures -------------------------------------------


@pytest.mark.parametrize(
    "attribute,value",
    [("jahre", (2020, 2021)), ("offen", True), ("normen", ["§ 8c KStG"]), ("steuerarten", ["KSt"])],
)
async def test_changed_judge_inputs_are_judged_again(settings, project, attribute, value):
    repo, client, screener = await screened(settings, project)
    assert (await screener.screen(project))[0].skipped == 1 and client.completions.calls == 1

    setattr(repo.embeddings[project][0], attribute, value)  # the sentence stays the same
    repo.states[project].profile_version = 2
    (report,) = await screener.screen(project)

    assert report.judged == 1 and report.skipped == 0 and client.completions.calls == 2
    (match,) = await repo.list_matches(project)
    assert match.profile_version == 2 and match.is_current


async def test_newly_stronger_feature_is_judged(settings, project):
    repo = await ready(settings, project, features=[feature(settings, 1, [0.6, 0.8])])
    stronger = "Die Gesellschaft verliert Verlustvorträge durch einen Anteilserwerb."

    def judge(kwargs):
        return verdict(stronger in kwargs["messages"][1]["content"], "Nur der neue Sachverhalt.")

    client = FakeClient(responder=judge)
    screener = Screener(repo, settings, client)
    (first,) = await screener.screen(project)
    assert first.hits == 0 and (await repo.list_matches(project))[0].objekt_id == 1

    new = feature(settings, 2, [1.0, 0.0]).model_copy(update={"text_basis": stronger})
    repo.embeddings[project].append(new)
    (second,) = await screener.screen(project)

    assert second.judged == 1 and second.hits == 1
    (match,) = await repo.list_matches(project)
    assert match.objekt_id == 2 and match.judge_relevant
    assert len(await repo.list_alerts(project)) == 1


async def test_unchanged_inputs_cost_no_call_and_keep_advisor_actions(settings, project):
    repo, client, screener = await screened(settings, project)
    (alert,) = await repo.list_alerts(project)
    await alerts.mark_seen(repo, alert.id)
    repo.states[project].profile_version = 5  # for example a contact edit

    (report,) = await screener.screen(project)

    assert report.skipped == 1 and report.judged == 0 and client.completions.calls == 1
    (match,) = await repo.list_matches(project)
    assert match.profile_version == 5 and match.is_current
    assert (await repo.get_alert(alert.id)).status is AlertStatus.SEEN


async def test_deferred_sections_wait_for_the_next_request(settings, project):
    settings = settings.model_copy(update={"match_max_candidates": 2})
    repo = await ready(settings, project, sections=[section(settings, n) for n in (1, 2, 3)])
    client = FakeClient(text=verdict())
    screener = Screener(repo, settings, client)

    (first,) = await screener.screen(project)

    assert first.judged == 2 and first.deferred == ["Rz. 3"] and not first.complete
    status = await repo.screening_status(project)
    assert status.incomplete_laws and status.pending

    (second,) = await screener.screen(project)  # cached sections cost no budget

    assert second.judged == 1 and second.skipped == 2 and second.complete
    assert client.completions.calls == 3 and len(await repo.list_matches(project)) == 3
    assert not (await repo.screening_status(project)).pending


async def test_deferred_earlier_decision_becomes_non_current(settings, project):
    settings = settings.model_copy(update={"match_max_candidates": 1})
    sections = [section(settings, 1), section(settings, 2, [0.9, 0.1])]
    repo = await ready(settings, project, sections=sections)
    client = FakeClient(text=verdict())
    screener = Screener(repo, settings, client)
    await screener.screen(project)
    await screener.screen(project)  # both judged now
    assert len(await repo.list_matches(project)) == 2

    repo.embeddings[project][0].offen = True  # both sections change
    (report,) = await screener.screen(project)

    assert report.deferred == ["Rz. 2"] and report.judged == 1
    current = await repo.list_matches(project)
    assert [m.section_ref for m in current] == ["Rz. 1"]
    (waiting,) = [
        m for m in await repo.list_matches(project, include_history=True) if not m.is_current
    ]
    assert waiting.invalidation_reason is Invalidation.DEFERRED
    alert = next(a for a in await repo.list_alerts(project) if a.section_ref == "Rz. 2")
    assert alert.status is AlertStatus.NEW  # pending: not obsolete, but not sendable
    with pytest.raises(StateError, match="not current"):
        await email.prepare_email(repo, settings, alert.id)


async def test_judge_failure_is_retryable_and_completed_sections_are_reused(settings, project):
    settings = one_per_section(settings)
    repo = await ready(
        settings,
        project,
        features=[feature(settings), feature(settings, 2, [0, 1])],
        sections=[section(settings), section(settings, 2, [0, 1])],
    )
    failing = {"on": True}

    def judge(kwargs):
        if failing["on"] and "Abschnitt: Rz. 1\n" in kwargs["messages"][1]["content"]:
            return "invalid"
        return verdict()

    client = FakeClient(responder=judge)
    screener = Screener(repo, settings, client)
    (first,) = await screener.screen(project)
    assert first.unjudged == ["Rz. 1"] and first.stored == 1 and not first.complete
    calls = client.completions.calls

    failing["on"] = False
    (retry,) = await screener.screen(project)

    assert retry.judged == 1 and retry.skipped == 1 and retry.complete
    assert client.completions.calls == calls + 1


async def test_legacy_decision_needs_one_explicit_pass(settings, project):
    repo = await ready(settings, project)
    section_id = next(iter(repo.sections))
    legacy = Match(
        company_id=project,
        law_id=next(iter(repo.laws)),
        law_section_id=section_id,
        objekt_typ="finding",
        objekt_id=1,
        feature_text=repo.embeddings[project][0].text_basis,
        score=1.0,
        judge_relevant=True,
        judge_reason="Alt.",
        model="old",
    )
    await repo.save_matches([legacy])
    (alert,) = await repo.list_alerts(project)
    stored = repo.matches[(project, section_id)]
    stored.is_current, stored.invalidation_reason = False, Invalidation.MIGRATED
    stored.input_fingerprint = None
    assert (await repo.screening_status(project)).stale_decisions == 1
    with pytest.raises(StateError, match="not current"):
        await email.prepare_email(repo, settings, alert.id)

    client = FakeClient(text=verdict())
    (report,) = await Screener(repo, settings, client).screen(project)

    assert report.judged == 1 and client.completions.calls == 1
    assert (await repo.get_alert(alert.id)).status is AlertStatus.NEW
    assert (await email.prepare_email(repo, settings, alert.id)).alert_id == alert.id


# Postgres parity and the migration -------------------------------------------------------


async def test_postgres_reconciliation_fingerprint_and_migration(
    pg_repository, profil_schema, settings, project, tmp_path
):
    repo = pg_repository
    view = await published(repo, project, profil_schema, settings)
    await confirm_profile(repo, profil_schema, settings, decide_all(view))
    await Embedder(
        repo, profil_schema, settings, FakeClient(text="Die Gesellschaft hat Verlustvorträge.")
    ).run(project)
    ingested = await ingest_law(
        law_pdf(tmp_path, f"{project}.pdf"), repo, settings, FakeClient(text=_metadata())
    )
    url = os.environ["TEST_DATABASE_URL"]
    try:
        client = FakeClient(text=verdict())
        screener = Screener(repo, settings, client)
        (first,) = await screener.screen(project, ingested.law_id)
        hits = await repo.list_matches(project, only_hits=True, law_id=ingested.law_id)
        assert first.hits == len(hits) > 0 and all(m.input_fingerprint for m in hits)
        calls = client.completions.calls
        assert (await screener.screen(project, ingested.law_id))[0].skipped == len(hits)
        assert client.completions.calls == calls
        status = await repo.screening_status(project)
        assert ingested.law_id not in status.unscreened_laws + status.incomplete_laws

        # Rows from before the migration: no fingerprint, not current, one pass needed.
        async with await psycopg.AsyncConnection.connect(url, autocommit=True) as conn:
            await conn.execute(
                "UPDATE screening.match SET is_current = false, input_fingerprint = NULL, "
                "invalidation_reason = NULL WHERE company_id = %s",
                (project,),
            )
        await repo.initialize()
        await repo.initialize()  # idempotent
        legacy = await repo.list_matches(project, include_history=True)
        assert {m.invalidation_reason for m in legacy} == {Invalidation.MIGRATED}
        assert await repo.list_matches(project) == []
        alert = (await repo.list_alerts(project, ingested.law_id))[0]
        with pytest.raises(StateError, match="not current"):
            await email.prepare_email(repo, settings, alert.id)
        await screener.screen(project, ingested.law_id)
        assert len(await repo.list_matches(project, only_hits=True)) == len(hits)
        assert (await repo.get_alert(alert.id)).status is AlertStatus.NEW

        # Removing every feature reconciles all of them atomically with their alerts.
        await alerts.dismiss(repo, alert.id, "erledigt")
        async with await psycopg.AsyncConnection.connect(url, autocommit=True) as conn:
            await conn.execute(
                "DELETE FROM profil.embedding WHERE gesellschaft_id = %s",
                ((await repo.company_features(project)).gesellschaft_id,),
            )
        (removed,) = await screener.screen(project, ingested.law_id)
        assert removed.invalidated == len(hits) and await repo.list_matches(project) == []
        statuses = {a.id: a.status for a in await repo.list_alerts(project, ingested.law_id)}
        assert statuses.pop(alert.id) is AlertStatus.DISMISSED
        assert set(statuses.values()) <= {AlertStatus.OBSOLETE}
        history = await repo.list_matches(project, include_history=True)
        assert all(m.invalidation_reason is Invalidation.NOT_CANDIDATE for m in history)
        assert all(m.judge_relevant for m in history)
    finally:
        async with await psycopg.AsyncConnection.connect(url, autocommit=True) as conn:
            await conn.execute("DELETE FROM screening.alert WHERE company_id = %s", (project,))
            await conn.execute("DELETE FROM screening.law WHERE id = %s", (ingested.law_id,))


def _metadata() -> str:
    import json

    return json.dumps(METADATA)
