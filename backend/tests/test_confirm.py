"""Step 8: review and confirm. Memory repository for the rules, Postgres (skipped without
TEST_DATABASE_URL) for stamps, closed hints and the rollback."""

import os
from datetime import date

import psycopg
import pytest

from app.db.memory import MemoryRepository
from app.db.repository import StaleDraftError, StateError
from app.models.profile import DecisionAction
from app.models.project import ProjectState
from app.models.review import CellChoice, ConfirmRequest, Decision
from app.profile.review import DecisionError, apply_decisions, open_decisions
from app.services.confirm import confirm_profile, review_profile
from tests.profil_data import (
    QUOTE,
    decide_all,
    draft_for,
    extractions,
    fact_rows,
    with_conflict,
)

TODAY = date(2026, 10, 17)
ROW_KEY = {"art": "kst_verlustvortrag", "stichtag": "2024-12-31"}


async def published(repository, project, profil_schema, settings, cands=None):
    """Save the draft and publish it unconfirmed (end of step 7)."""
    draft = draft_for(project, profil_schema, cands)
    await repository.save_profile_draft(draft)
    await repository.publish_profile(draft, extractions(project), profil_schema, settings)
    for e in extractions(project):
        await repository.replace_document_candidates(e)
    return await review_profile(repository, profil_schema, settings, project)


def decision(view, field, **kwargs):
    """The complete decisions file with one decision replaced."""
    request = decide_all(view)
    request.decisions = [d for d in request.decisions if d.field != field]
    request.decisions.append(Decision(field=field, **kwargs))
    return request


async def test_open_decisions_cover_conflicts_missing_and_rows(profil_schema, project):
    draft = draft_for(project, profil_schema, with_conflict(project))

    open_ = {(o.field, o.kind): o for o in open_decisions(draft, profil_schema, 1000)}

    assert ("kaufpreis_eur", "conflict") in open_
    assert ("verkaeufer", "missing") in open_
    row = open_[("steuerattribut", "row_conflict")]
    assert row.key == ROW_KEY and row.cells == ["betrag_eur"]
    assert ("kaeufer", "single_source") not in open_  # agreed values need no decision
    assert not any(field == "merkmal" for field, _ in open_)


async def test_happy_path_memory(profil_schema, settings, project):
    repository = MemoryRepository()
    view = await published(repository, project, profil_schema, settings, with_conflict(project))
    assert view.state is ProjectState.PUBLISHED and view.open_hints

    result = await confirm_profile(
        repository, profil_schema, settings, decide_all(view), today=TODAY
    )

    state = await repository.get_project_state(project)
    assert state.state is ProjectState.CONFIRMED and state.confirmed_by == "Dr. Test"
    assert state.draft_version == view.draft_version
    assert result.conflicts_resolved == 2  # kaufpreis_eur and the steuerattribut row
    assert result.fields_left_empty > 0 and result.fields_accepted > 0
    assert result.hints_closed == 2 and result.hints_open == 0
    confirmed = repository.published[project]
    assert confirmed.field("kaufpreis_eur").decision.action is DecisionAction.CHOOSE


async def test_suggestion_is_never_preselected(profil_schema, settings, project):
    repository = MemoryRepository()
    view = await published(repository, project, profil_schema, settings, with_conflict(project))
    request = decide_all(view)
    request.decisions = [d for d in request.decisions if d.field != "kaufpreis_eur"]

    with pytest.raises(DecisionError) as error:
        await confirm_profile(repository, profil_schema, settings, request)

    assert [e.field for e in error.value.errors] == ["kaufpreis_eur"]
    assert "decision missing" in error.value.errors[0].message


async def test_refusals_list_every_problem_and_change_nothing(profil_schema, settings, project):
    repository = MemoryRepository()
    view = await published(repository, project, profil_schema, settings)
    request = decide_all(view)
    replaced = ("verkaeufer", "signing_am", "dd_art", "dd_zeitraum_von")
    request.decisions = [d for d in request.decisions if d.field not in replaced]
    request.decisions += [  # verkaeufer: no decision at all
        Decision(field="no_such_field", action=DecisionAction.LEAVE_EMPTY),
        Decision(field="signing_am", action=DecisionAction.VALUE, value="17.10.2026"),
        Decision(field="dd_art", action=DecisionAction.VALUE, value="other"),
        Decision(field="dd_zeitraum_von", action=DecisionAction.VALUE, value="2021"),
    ]

    with pytest.raises(DecisionError) as error:
        await confirm_profile(repository, profil_schema, settings, request)

    problems = {e.field: e.message for e in error.value.errors}
    assert problems["verkaeufer"].startswith("decision missing")
    assert problems["no_such_field"] == "unknown field"
    assert "ISO date" in problems["signing_am"]
    assert "must be one of" in problems["dd_art"]
    assert problems["dd_zeitraum_von"] == "expected a whole number"
    state = await repository.get_project_state(project)
    assert state.state is ProjectState.PUBLISHED  # nothing changed


async def test_wrong_state_and_stale_version(profil_schema, settings, project):
    repository = MemoryRepository()
    draft = draft_for(project, profil_schema)
    await repository.save_profile_draft(draft)
    view = await review_profile(repository, profil_schema, settings, project)

    with pytest.raises(StateError, match="confirm needs state published"):
        await confirm_profile(repository, profil_schema, settings, decide_all(view))

    view = await published(repository, project, profil_schema, settings)
    await repository.save_profile_draft(draft_for(project, profil_schema))  # a rerun
    with pytest.raises(StaleDraftError):
        await confirm_profile(repository, profil_schema, settings, decide_all(view))
    assert (await repository.get_project_state(project)).state is ProjectState.PUBLISHED


def test_each_decision_type(profil_schema, project):
    draft = draft_for(project, profil_schema, with_conflict(project))
    view_like = type("V", (), {})()
    view_like.open_decisions = open_decisions(draft, profil_schema, 1000)
    view_like.company_id, view_like.draft_version = project, "v"
    report_price = [g.value for g in draft.field("kaufpreis_eur").groups].index(38_000_000)
    request = decide_all(view_like)
    request.decisions = [
        d for d in request.decisions if d.field not in ("kaufpreis_eur", "verkaeufer", "ort")
    ] + [
        Decision(field="kaufpreis_eur", action=DecisionAction.CHOOSE, group=report_price),
        Decision(field="verkaeufer", action=DecisionAction.VALUE, value="Henrik Albers"),
        Decision(field="ort", action=DecisionAction.LEAVE_EMPTY),
        Decision(
            field="darlehen",
            key={"darlehensgeber": "Henrik Albers", "beginn_am": "2017-07-01"},
            action=DecisionAction.DROP,
        ),
    ]

    confirmed, result = apply_decisions(draft, profil_schema, request, 1000)

    price = confirmed.field("kaufpreis_eur")
    assert price.chosen == 38_000_000 and price.chosen_group.candidates[0].source_doc == "report"
    seller = confirmed.field("verkaeufer")
    assert seller.chosen == "Henrik Albers" and seller.entered_by_advisor
    assert confirmed.field("ort").chosen is None and confirmed.field("ort").dropped
    (loan,) = confirmed.field("darlehen").items
    assert loan.dropped
    (row,) = confirmed.field("steuerattribut").items
    assert row.chosen["betrag_eur"] == 2_412_380 and row.chosen["festgestellt"] is True
    assert row.chosen_group.candidates[0].source_doc == "notice"  # provenance kept
    assert result.values_typed == 1 and result.rows_dropped == 1
    assert draft.field("kaufpreis_eur").decision is None  # the stored draft is untouched


def test_row_edit_typed_cell_and_required_rules(profil_schema, project):
    draft = draft_for(project, profil_schema, with_conflict(project))
    base = ConfirmRequest(company_id=project, draft_version="v", confirmed_by="X")

    def errors(*decisions):
        request = base.model_copy(update={"decisions": list(decisions)})
        with pytest.raises(DecisionError) as error:
            apply_decisions(draft, profil_schema, request, 1000)
        return {(e.field, e.message) for e in error.value.errors}

    found = errors(
        Decision(field="kaeufer", action=DecisionAction.LEAVE_EMPTY),
        Decision(field="steuerattribut", key=ROW_KEY, action=DecisionAction.ACCEPT),
        Decision(field="beteiligung", key={"gesellschafter": "x"}, action=DecisionAction.DROP),
    )
    assert ("kaeufer", "a required field cannot be left empty") in found
    assert ("steuerattribut", "a conflicting row needs edit or drop") in found
    assert ("beteiligung", "no such row in the draft") in found

    found = errors(
        Decision(
            field="steuerattribut",
            key=ROW_KEY,
            action=DecisionAction.EDIT,
            cells={"betrag_eur": CellChoice(value="viel")},
        )
    )
    assert ("steuerattribut", "betrag_eur: expected a number") in found


async def test_republish_refused_after_confirm(profil_schema, settings, project):
    repository = MemoryRepository()
    view = await published(repository, project, profil_schema, settings)
    await confirm_profile(repository, profil_schema, settings, decide_all(view))

    with pytest.raises(StateError, match="never overwritten"):
        await repository.publish_profile(
            draft_for(project, profil_schema), extractions(project), profil_schema, settings
        )


# Postgres -------------------------------------------------------------------------------


async def query(sql, *params):
    async with await psycopg.AsyncConnection.connect(os.environ["TEST_DATABASE_URL"]) as conn:
        cur = await conn.execute(sql, params)
        return await cur.fetchall()


async def profile_rows(project):
    return await fact_rows(os.environ["TEST_DATABASE_URL"], project)


async def test_confirm_end_to_end_postgres(pg_repository, profil_schema, settings, project):
    view = await published(pg_repository, project, profil_schema, settings, with_conflict(project))
    assert len(view.open_hints) == 3  # two conflicts and the skipped loan
    request = decision(view, "verkaeufer", action=DecisionAction.VALUE, value="Henrik Albers")

    result = await confirm_profile(pg_repository, profil_schema, settings, request, today=TODAY)

    assert result.rows["gesellschaft"] == 1 and result.hints_closed == 2
    assert result.hints_open == 1  # the skipped loan (plausibilitaet) stays open
    rows = await profile_rows(project)
    assert rows and all(by == "Dr. Test" and on == TODAY for _, _, by, on in rows)
    assert not any(QUOTE in text for _, text, _, _ in rows)
    hints = await query(
        "SELECT p.art, p.status, p.entscheidung FROM profil.pruefhinweis p "
        "JOIN profil.gesellschaft g ON g.id = p.gesellschaft_id "
        "JOIN profil.deal d ON d.id = g.deal_id WHERE d.projektname = %s ORDER BY p.id",
        project,
    )
    closed = [h for h in hints if h[1] == "geklaert"]
    assert len(closed) == 2 and all("bestätigt von Dr. Test" in h[2] for h in closed)
    assert any("Gewählt: 38000000 (01_Tax-DD-Report.pdf S. 3)" in h[2] for h in closed)
    assert not any(QUOTE in (h[2] or "") for h in hints)
    (deal,) = await query("SELECT verkaeufer FROM profil.deal WHERE projektname = %s", project)
    assert deal == ("Henrik Albers",)
    merkmale = dict(
        await query(
            "SELECT m.merkmal_code, m.wert::text FROM profil.merkmal_wert m "
            "JOIN profil.gesellschaft g ON g.id = m.gesellschaft_id "
            "JOIN profil.deal d ON d.id = g.deal_id WHERE d.projektname = %s",
            project,
        )
    )
    assert merkmale["E05"] == "unbekannt" and "nein" not in {
        v for k, v in merkmale.items() if k != "B04"
    }
    (state,) = await query(
        "SELECT state, confirmed_by FROM pipeline.project_state WHERE company_id = %s", project
    )
    assert state == ("confirmed", "Dr. Test")

    with pytest.raises(StateError, match="never overwritten"):
        await pg_repository.publish_profile(
            draft_for(project, profil_schema), extractions(project), profil_schema, settings
        )


async def test_typed_value_too_long_rolls_back_postgres(
    pg_repository, profil_schema, settings, project
):
    view = await published(pg_repository, project, profil_schema, settings)
    request = decision(view, "land", action=DecisionAction.VALUE, value="Deutschland")

    with pytest.raises(DecisionError) as error:
        await confirm_profile(pg_repository, profil_schema, settings, request)

    assert [(e.field, e.message) for e in error.value.errors] == [
        ("land", "too long (at most 2 characters)")
    ]
    (state,) = await query(
        "SELECT state FROM pipeline.project_state WHERE company_id = %s", project
    )
    assert state == ("published",)
    stamped = [r for r in await profile_rows(project) if r[2] is not None]
    assert stamped == []  # rolled back: still the unconfirmed rows


async def test_dropped_merkmal_becomes_unbekannt_postgres(
    pg_repository, profil_schema, settings, project
):
    view = await published(pg_repository, project, profil_schema, settings)
    request = decide_all(view)
    request.decisions.append(
        Decision(field="merkmal", key={"merkmal_code": "B04"}, action=DecisionAction.DROP)
    )

    await confirm_profile(pg_repository, profil_schema, settings, request)

    (b04,) = await query(
        "SELECT m.wert::text FROM profil.merkmal_wert m JOIN profil.gesellschaft g "
        "ON g.id = m.gesellschaft_id JOIN profil.deal d ON d.id = g.deal_id "
        "WHERE d.projektname = %s AND m.merkmal_code = 'B04'",
        project,
    )
    assert b04 == ("unbekannt",)  # silence is never nein
