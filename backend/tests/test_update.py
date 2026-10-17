"""Plan section 4b: advisor updates after confirmation. Request checks, busy marker and the
embedding refresh by diff run offline (memory); the update itself runs against Postgres
(skipped without TEST_DATABASE_URL), on a pipeline-confirmed project so the seed stays
untouched."""

import asyncio
import os
from datetime import date

import openai
import psycopg
import pytest

from app.db.repository import BusyError, RepositoryError, StaleProfileError, StateError
from app.models.audit import AuditFilter, AuditKind
from app.models.profile import AppliedDecision, DecisionAction
from app.models.project import ProjectState
from app.models.update import Change, ChangeKind, UpdateRequest
from app.profile.update import UpdateError, check_request
from app.services.cleanup import cleanup_project
from app.services.confirm import confirm_profile
from app.services.embed import Embedder
from app.services.update import read_profile, update_profile
from tests.helpers import FakeClient, api_error
from tests.profil_data import QUOTE, decide_all, fact_rows
from tests.test_confirm import published
from tests.test_embed import GOOD, chat, confirmed

UPDATED = date(2026, 11, 2)
LOAN_KEY = {"darlehensgeber": "Henrik Albers", "beginn_am": "2017-07-01"}


def request(project, *changes, version=1, by="Dr. Update"):
    return UpdateRequest(
        company_id=project, profile_version=version, changed_by=by, changes=list(changes)
    )


# Offline ------------------------------------------------------------------------------------


def test_request_checks_list_every_problem(profil_schema):
    bad = request(
        "x",
        Change(field="nope", kind=ChangeKind.EDIT, value=1),
        Change(field="kaufpreis_eur", kind=ChangeKind.ADD, value=1),
        Change(field="rechtsform", kind=ChangeKind.EDIT, value=None),
        Change(field="signing_am", kind=ChangeKind.EDIT, value="morgen"),
        Change(field="merkmal", key={"merkmal_code": "B04"}, kind=ChangeKind.REMOVE),
        Change(field="beteiligung", row_id=3, kind=ChangeKind.END),
        Change(field="darlehen", kind=ChangeKind.ADD, cells={"darlehensgeber": "X"}),
        Change(field="darlehen", row_id=1, kind=ChangeKind.EDIT, cells={"farbe": "rot"}),
        Change(field="finding", kind=ChangeKind.ADD, row_id=5, cells={"titel": "t"}),
    )

    messages = {(e.field, e.message) for e in check_request(bad, profil_schema)}

    assert ("nope", "unknown field") in messages
    assert ("kaufpreis_eur", "a scalar field can only be edited, not add") in messages
    assert ("rechtsform", "a required field cannot be emptied") in messages
    assert ("signing_am", "expected an ISO date (YYYY-MM-DD)") in messages
    assert any("never removed" in m for f, m in messages if f == "merkmal")
    assert ("beteiligung", "end needs end_date as ISO date (YYYY-MM-DD)") in messages
    assert any(m.startswith("a new row needs beziehung, betrag_eur") for f, m in messages)
    assert ("darlehen", "unknown column farbe") in messages
    assert ("finding", "a new row takes no row_id or key") in messages


async def test_updates_need_postgres(profil_schema, settings, project):
    repository = await confirmed(project, profil_schema, settings)
    change = Change(field="finanzamt", kind=ChangeKind.EDIT, value="FA Bremen")

    with pytest.raises(RepositoryError, match="postgres"):
        await update_profile(repository, profil_schema, settings, request(project, change), None)


async def test_busy_marker_blocks_a_second_step(profil_schema, settings, project):
    repository = await confirmed(project, profil_schema, settings)
    await repository.begin_step(project, "embed", 60)

    with pytest.raises(BusyError, match="embed running"):
        await repository.begin_step(project, "cleanup", 60)
    await repository.begin_step(project, "cleanup", 0)  # a stale marker is taken over
    await repository.end_step(project)
    assert (await repository.get_project_state(project)).running_step is None


async def test_refresh_by_diff(profil_schema, settings, project):
    repository = await confirmed(project, profil_schema, settings)
    await Embedder(repository, profil_schema, settings, FakeClient(responder=chat(GOOD))).run(
        project
    )
    before = {r.ref: r for r in repository.embeddings[project]}
    # Simulate an update of the stored profile: B07 ja -> nein, a new E05 ja.
    draft = repository.published[project]
    b07 = next(i for i in draft.field("merkmal").items if i.key == {"merkmal_code": "B07"})
    b07.decision = AppliedDecision(
        action=DecisionAction.EDIT, value=b07.chosen | {"wert": "nein"}, group=0, text="x"
    )
    new_draft_item = b07.model_copy(deep=True)
    new_draft_item.key = {"merkmal_code": "E05"}
    new_draft_item.decision = AppliedDecision(
        action=DecisionAction.EDIT,
        value={"merkmal_code": "E05", "wert": "ja", "kurztext": "Grundbesitz"},
        group=0,
        text="x",
    )
    draft.field("merkmal").items.append(new_draft_item)
    client = FakeClient(responder=chat("Die Gesellschaft besitzt betrieblichen Grundbesitz."))

    report = await Embedder(repository, profil_schema, settings, client).refresh(
        project, {"finding:1"}
    )

    after = {r.ref: r for r in repository.embeddings[project]}
    b07_ref = next(r for r in before if r.startswith("merkmal_wert"))
    assert b07_ref not in after  # ja -> nein removes the vector
    new_ref = next(r for r in after if r.startswith("merkmal_wert"))
    assert after[new_ref].text_basis.startswith("Die Gesellschaft besitzt")  # new feature
    assert after["finding:1"].text_basis != before["finding:1"].text_basis  # changed: rewritten
    appeal = "rechtsbehelf:1"
    assert after[appeal].vektor == before[appeal].vektor  # untouched: vector kept
    assert after[appeal].text_basis == GOOD
    assert report.reused == 1 and len(report.sentences) == 2
    assert client.completions.calls == 2 and client.embeddings.calls == 1


async def test_refresh_before_first_embedding_is_a_no_op(profil_schema, settings, project):
    repository = await confirmed(project, profil_schema, settings)
    client = FakeClient()

    report = await Embedder(repository, profil_schema, settings, client).refresh(project, set())

    assert report.written == 0 and client.completions.calls == 0
    assert (await repository.get_project_state(project)).state is ProjectState.CONFIRMED


# Postgres -----------------------------------------------------------------------------------


async def query(sql, *params):
    async with await psycopg.AsyncConnection.connect(os.environ["TEST_DATABASE_URL"]) as conn:
        cur = await conn.execute(sql, params)
        return await cur.fetchall() if cur.description else None


async def pg_confirmed(pg_repository, project, profil_schema, settings):
    view = await published(pg_repository, project, profil_schema, settings)
    await confirm_profile(
        pg_repository, profil_schema, settings, decide_all(view), today=date(2026, 10, 17)
    )


def finding_k9():
    return {
        "finding_code": "K-9",
        "titel": "Umsatzsteuerliche Organschaft geplant",
        "steuerarten": ["USt"],
        "risiko": "gering",
        "richtung": "chance",
        "status": "offen",
        "normen": ["§ 2 Abs. 2 Nr. 2 UStG"],
    }


async def test_each_kind_of_change_postgres(pg_repository, profil_schema, settings, project):
    await pg_confirmed(pg_repository, project, profil_schema, settings)
    loan = {
        "darlehensgeber": "Henrik Albers",
        "beziehung": "gesellschafter",
        "betrag_eur": 500_000,
        "status": "laufend",
        "beginn_am": "2017-07-01",
    }

    result = await update_profile(
        pg_repository,
        profil_schema,
        settings,
        request(
            project,
            Change(field="finanzamt", kind=ChangeKind.EDIT, value="Finanzamt Bremen"),
            Change(
                field="merkmal",
                key={"merkmal_code": "B07"},
                kind=ChangeKind.EDIT,
                cells={"wert": "nein", "kurztext": "Tochter verkauft"},
            ),
            Change(field="darlehen", kind=ChangeKind.ADD, cells=loan),  # was skipped at publish
            Change(field="finding", kind=ChangeKind.ADD, cells=finding_k9()),
            Change(field="organschaft", key={"art": "kst_gewst"}, kind=ChangeKind.REMOVE),
        ),
        client=None,
        today=UPDATED,
    )

    assert result.profile_version == 2 and result.changes == 5
    assert result.hints_closed == 1  # the plausibilitaet hint of the skipped loan
    assert not result.embeddings_stale  # not embedded yet: nothing to refresh
    assert any(r.startswith("finding:") for r in result.changed_features)
    rows = await fact_rows(os.environ["TEST_DATABASE_URL"], project)
    stamped = [(t, text) for t, text, by, on in rows if by == "Dr. Update"]
    assert {t for t, _ in stamped} == {"gesellschaft", "merkmal_wert", "darlehen", "finding"}
    assert all(on == UPDATED for _, _, by, on in rows if by == "Dr. Update")
    untouched = [r for r in rows if r[0] == "veranlagung"]
    assert untouched and all(r[2] == "Dr. Test" for r in untouched)  # not modified
    (b07,) = await query(
        "SELECT m.wert::text, m.fundstelle, m.quelle_dok, m.konfidenz::text FROM "
        "profil.merkmal_wert m JOIN profil.gesellschaft g ON g.id = m.gesellschaft_id "
        "JOIN profil.deal d ON d.id = g.deal_id WHERE d.projektname = %s "
        "AND m.merkmal_code = 'B07'",
        project,
    )
    assert b07 == ("nein", f"Vom Steuerberater eingegeben am {UPDATED}", None, "hoch")
    norms = await query(
        "SELECT n.norm_code FROM profil.finding_norm n JOIN profil.finding f ON f.id = "
        "n.finding_id JOIN profil.deal d ON d.id = f.deal_id WHERE d.projektname = %s "
        "AND f.finding_code = 'K-9'",
        project,
    )
    assert norms == [("§ 2 Abs. 2 Nr. 2 UStG",)]
    (ges,) = await query(
        "SELECT g.finanzamt, g.fundstelle FROM profil.gesellschaft g JOIN profil.deal d "
        "ON d.id = g.deal_id WHERE d.projektname = %s",
        project,
    )
    assert ges[0] == "Finanzamt Bremen" and "finanzamt vom steuerberater" in ges[1]

    profile = await read_profile(pg_repository, profil_schema, settings, project)
    assert profile.profile_version == 2 and profile.scalars["finanzamt"] == "Finanzamt Bremen"
    assert profile.lists["organschaft"] == []
    changed = [e for e in profile.audit if e.kind is AuditKind.PROFILE_CHANGED]
    history = {(e.change_kind, e.field): e for e in changed}
    assert history[("edit", "finanzamt")].new_value == "Finanzamt Bremen"
    assert history[("edit", "merkmal")].old_value == {
        "wert": "ja",
        "kurztext": "Tochter Nordhafen Logistics B.V. (NL), 100 %",
    }
    assert history[("remove", "organschaft")].old_value["status"] == "geplant"
    assert len(changed) == 5 and {e.update_id for e in changed} == {result.update_id}
    assert all(
        e.profile_version == 2 and e.screened_at is None and e.acted_by == "Dr. Update"
        for e in changed
    )
    (confirmed_entry,) = [e for e in profile.audit if e.kind is AuditKind.PROFILE_CONFIRMED]
    assert confirmed_entry.acted_by == "Dr. Test" and confirmed_entry.profile_version == 1
    assert confirmed_entry.new_value["counts"]["fields_left_empty"] > 0
    assert profile.audit[0].acted_at >= profile.audit[-1].acted_at  # newest first

    # Back to ja, and end-dating needs a table with a validity end.
    shareholder = {
        "gesellschafter": "Henrik Albers",
        "gesellschafter_art": "natuerliche_person",
        "quote_prozent": 60,
        "gueltig_ab": "2009-04-03",
    }
    await update_profile(
        pg_repository,
        profil_schema,
        settings,
        request(
            project,
            Change(
                field="merkmal",
                key={"merkmal_code": "B07"},
                kind=ChangeKind.EDIT,
                cells={"wert": "ja"},
            ),
            Change(field="beteiligung", kind=ChangeKind.ADD, cells=shareholder),
            version=2,
        ),
        client=None,
    )
    row_id = (
        (await read_profile(pg_repository, profil_schema, settings, project))
        .lists["beteiligung"][0]
        .id
    )
    ended = await update_profile(
        pg_repository,
        profil_schema,
        settings,
        request(
            project,
            Change(field="beteiligung", row_id=row_id, kind=ChangeKind.END, end_date="2026-05-12"),
            version=3,
        ),
        client=None,
    )
    assert ended.profile_version == 4
    (end,) = await query("SELECT gueltig_bis FROM profil.beteiligung WHERE id = %s", row_id)
    assert end == (date(2026, 5, 12),)


async def test_refusals_change_nothing_postgres(pg_repository, profil_schema, settings, project):
    view = await published(pg_repository, project, profil_schema, settings)
    edit = Change(field="finanzamt", kind=ChangeKind.EDIT, value="FA")

    with pytest.raises(StateError, match="updates need a confirmed profile"):
        await update_profile(pg_repository, profil_schema, settings, request(project, edit), None)

    await confirm_profile(pg_repository, profil_schema, settings, decide_all(view))
    with pytest.raises(StaleProfileError):
        await update_profile(
            pg_repository, profil_schema, settings, request(project, edit, version=7), None
        )
    with pytest.raises(UpdateError) as error:
        await update_profile(
            pg_repository,
            profil_schema,
            settings,
            request(
                project,
                edit,
                Change(field="land", kind=ChangeKind.EDIT, value="Deutschland"),
                Change(field="darlehen", key=LOAN_KEY, kind=ChangeKind.REMOVE),
            ),
            None,
        )
    problems = {(e.field, e.message) for e in error.value.errors}
    assert problems == {
        ("land", "land: too long (at most 2 characters)"),
        ("darlehen", "no such row"),
    }
    await pg_repository.begin_step(project, "embed", 3600)
    with pytest.raises(BusyError):
        await update_profile(pg_repository, profil_schema, settings, request(project, edit), None)
    await pg_repository.end_step(project)

    state = await pg_repository.get_project_state(project)
    changed = AuditFilter(company_id=project, kind=AuditKind.PROFILE_CHANGED)
    assert state.profile_version == 1 and await pg_repository.list_audit(changed) == []
    (ges,) = await query(
        "SELECT g.finanzamt FROM profil.gesellschaft g JOIN profil.deal d ON d.id = g.deal_id "
        "WHERE d.projektname = %s",
        project,
    )
    assert ges == (None,)


async def test_refresh_failure_keeps_change_and_marks_stale_postgres(
    pg_repository, profil_schema, settings, project
):
    await pg_confirmed(pg_repository, project, profil_schema, settings)
    await Embedder(pg_repository, profil_schema, settings, FakeClient(responder=chat(GOOD))).run(
        project
    )
    await cleanup_project(pg_repository, settings, project)
    broken = FakeClient(
        responder=chat(GOOD),
        embedding_errors=[api_error(openai.InternalServerError, 500)] * 10,
    )
    change = Change(
        field="merkmal",
        key={"merkmal_code": "E05"},
        kind=ChangeKind.EDIT,
        cells={"wert": "ja", "kurztext": "Grundbesitz"},
    )

    result = await update_profile(
        pg_repository, profil_schema, settings, request(project, change), broken
    )

    assert result.profile_version == 2 and result.embeddings_stale and result.refresh_error
    state = await pg_repository.get_project_state(project)
    assert state.embeddings_stale and state.state is ProjectState.CLEANED

    report = await Embedder(  # the repair (what --advance runs)
        pg_repository, profil_schema, settings, FakeClient(responder=chat(GOOD))
    ).run(project)

    state = await pg_repository.get_project_state(project)
    assert not state.embeddings_stale and state.state is ProjectState.CLEANED
    assert report.written == 4  # B07, the new E05, the finding, the appeal
    audit = await pg_repository.list_audit(AuditFilter(company_id=project))
    kinds = [e.kind for e in audit]  # survives step 10, newest first
    assert kinds == [AuditKind.PROFILE_CHANGED, AuditKind.PROFILE_CONFIRMED]
    assert QUOTE not in str([e.model_dump() for e in audit])


async def test_refresh_after_update_by_diff_postgres(
    pg_repository, profil_schema, settings, project
):
    await pg_confirmed(pg_repository, project, profil_schema, settings)
    await Embedder(pg_repository, profil_schema, settings, FakeClient(responder=chat(GOOD))).run(
        project
    )
    before = {
        r.ref: r for r in await pg_repository.list_embeddings(project, settings.embedding_model)
    }
    finding = next(r for r in before if r.startswith("finding:"))
    client = FakeClient(responder=chat("Ein steuerliches Risiko wurde neu bewertet."))

    result = await update_profile(
        pg_repository,
        profil_schema,
        settings,
        request(
            project,
            Change(
                field="finding",
                row_id=int(finding.split(":")[1]),
                kind=ChangeKind.EDIT,
                cells={"titel": "Verlustuntergang neu bewertet"},
            ),
            Change(
                field="merkmal",
                key={"merkmal_code": "B07"},
                kind=ChangeKind.EDIT,
                cells={"wert": "nein"},
            ),
        ),
        client,
    )

    assert result.embeddings_refreshed and not result.embeddings_stale
    after = {
        r.ref: r for r in await pg_repository.list_embeddings(project, settings.embedding_model)
    }
    assert after[finding].text_basis.startswith("Ein steuerliches Risiko")
    assert not any(r.startswith("merkmal_wert") for r in after)  # B07 now nein
    appeal = next(r for r in before if r.startswith("rechtsbehelf"))
    assert after[appeal].vektor == before[appeal].vektor and after[appeal].jahre == (2022, 2022)
    assert client.completions.calls == 1  # only the edited finding got a new sentence


@pytest.mark.skipif(not os.environ.get("TEST_DATABASE_URL"), reason="TEST_DATABASE_URL not set")
def test_cli_profile_update_history(
    profil_schema, settings, project, tmp_path, monkeypatch, capsys
):
    # Synchronous: the CLI runs its own event loop.
    from pydantic import SecretStr

    from app import cli
    from app.config import PROFILE_SCHEMA
    from app.db.postgres import PostgresRepository

    async def setup():
        repository = PostgresRepository(os.environ["TEST_DATABASE_URL"])
        try:
            await repository.initialize()
            await pg_confirmed(repository, project, profil_schema, settings)
        finally:
            await repository.close()

    asyncio.run(setup(), loop_factory=asyncio.SelectorEventLoop)
    cli_settings = settings.model_copy(
        update={
            "repository": "postgres",
            "database_url": SecretStr(os.environ["TEST_DATABASE_URL"]),
            "profile_schema_path": PROFILE_SCHEMA,
        }
    )
    monkeypatch.setattr(cli, "get_settings", lambda: cli_settings)
    changes = tmp_path / "changes.json"
    changes.write_text(
        '{"profile_version": 1, "changes": [{"field": "finanzamt", "kind": "edit", '
        '"value": "Finanzamt Bremen-Nord"}]}',
        "utf-8",
    )

    assert cli.main(["--update", str(changes), "--company-id", project]) == 2  # no --changed-by
    args = ["--update", str(changes), "--changed-by", "Dr. CLI", "--company-id", project]
    assert cli.main(args) == 0
    assert "profile version 2" in capsys.readouterr().out
    assert cli.main(args) == 1  # same file again: version 1 is stale now
    audit = ["--audit", "--company-id", project, "--kind", "profile_changed"]
    assert cli.main(audit) == 0
    printed = capsys.readouterr().out
    assert "Dr. CLI: profile_changed edit finanzamt" in printed and "1 entry" in printed
    assert cli.main([*audit, "--person", "Nobody"]) == 0
    assert "0 entries" in capsys.readouterr().out
    assert cli.main(["--profile", "--company-id", project]) == 0
    assert "Finanzamt Bremen-Nord" in capsys.readouterr().out


async def test_audit_log_is_append_only_postgres(pg_repository, profil_schema, settings, project):
    await pg_confirmed(pg_repository, project, profil_schema, settings)
    url = os.environ["TEST_DATABASE_URL"]
    for statement in (
        "UPDATE pipeline.audit_log SET acted_by = 'Mallory' WHERE company_id = %s",
        "DELETE FROM pipeline.audit_log WHERE company_id = %s",
    ):
        async with await psycopg.AsyncConnection.connect(url) as conn:
            with pytest.raises(psycopg.errors.RaiseException, match="append-only"):
                await conn.execute(statement, (project,))
    # The one allowed change: matching sets the screened marker once.
    await query("UPDATE pipeline.audit_log SET screened_at = now() WHERE company_id = %s", project)
    with pytest.raises(psycopg.errors.RaiseException):
        await query(
            "UPDATE pipeline.audit_log SET screened_at = now() WHERE company_id = %s", project
        )
    (entry,) = await pg_repository.list_audit(
        AuditFilter(company_id=project, acted_by="Dr. Test", kind=AuditKind.PROFILE_CONFIRMED)
    )
    assert entry.screened_at is not None and entry.acted_by == "Dr. Test"
    later = await pg_repository.list_audit(
        AuditFilter(company_id=project, since=entry.acted_at.replace(year=2099))
    )
    assert later == []


async def test_confirm_writes_audit_entry_memory(profil_schema, settings, project):
    repository = await confirmed(project, profil_schema, settings)

    (entry,) = await repository.list_audit(AuditFilter(company_id=project))

    assert entry.kind is AuditKind.PROFILE_CONFIRMED and entry.acted_by == "Dr. Test"
    assert entry.new_value["counts"]["fields_accepted"] > 0
    assert await repository.list_audit(AuditFilter(company_id=project, acted_by="X")) == []
