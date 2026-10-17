"""Steps 13–15: rules, max cosine, judge, reruns and Postgres end to end.

All model calls use FakeClient; the database test needs TEST_DATABASE_URL.
"""

import asyncio
import json
import math
import os
import uuid

import psycopg
import pytest

from app.db.memory import MemoryRepository
from app.db.repository import BusyError, StateError
from app.matching.judge import Judge, judge_message
from app.matching.rules import Pair, filter_pairs
from app.matching.score import MatchError, cosine, rank_sections
from app.models.audit import AuditEntry, AuditKind
from app.models.features import EmbeddingRecord
from app.models.law import Law, LawSection
from app.models.match import CompanyFeatures, Trigger
from app.models.project import ProjectState, ProjectStatus
from app.services.confirm import confirm_profile
from app.services.embed import Embedder
from app.services.law_ingest import ingest_law
from app.services.screen import Screener
from tests.helpers import FakeClient, Reply
from tests.profil_data import decide_all
from tests.test_confirm import published
from tests.test_laws import METADATA, law_pdf


def feature(settings, nr=1, vector=None, **kwargs):
    return EmbeddingRecord(
        objekt_typ="finding",
        objekt_id=nr,
        gesellschaft_id=1,
        modell=settings.embedding_model,
        vektor=[1.0, 0.0] if vector is None else vector,
        text_basis="Die Gesellschaft hat körperschaftsteuerliche Verlustvorträge.",
        **kwargs,
    )


def law(**kwargs):
    return Law(
        id=1,
        kind="bmf_schreiben",
        title="Verlustabzug",
        status="in_kraft",
        filename="gesetz.pdf",
        content_hash="test",
        **kwargs,
    )


def section(settings, nr=1, vector=None, **kwargs):
    return LawSection(
        id=nr,
        law_id=1,
        nr=nr,
        section_ref=f"Rz. {nr}",
        text="Der Verlustabzug nach § 8c KStG entfällt beim schädlichen Beteiligungserwerb.",
        model=settings.embedding_model,
        vector=[1.0, 0.0] if vector is None else vector,
        **kwargs,
    )


def verdict(relevant=True, reason="Rz. 1 betrifft die Verlustvorträge bei Anteilsübertragung."):
    return json.dumps(
        {
            "relevant": relevant,
            "direction": "risiko" if relevant else "neutral",
            "reason": reason,
            "confidence": 0.9,
        }
    )


async def ready(settings, project, *, features=None, sections=None):
    repo = MemoryRepository()
    repo.states[project] = ProjectStatus(
        company_id=project,
        state=ProjectState.EMBEDDED,
        profile_version=1,
    )
    repo.embeddings[project] = features if features is not None else [feature(settings)]
    await repo.save_law(
        law(),
        sections if sections is not None else [section(settings)],
        replace=False,
    )
    return repo


@pytest.mark.parametrize(
    ("form", "allowed", "count", "unknown"),
    [
        ("GmbH & Co. KG", ["Kapitalgesellschaften"], 0, False),
        ("G.m.b.H.", ["Kapitalgesellschaften"], 1, False),
        (None, ["GmbH"], 1, True),
        ("GmbH & Co. KG", [], 1, False),
    ],
)
def test_legal_form_filter(settings, form, allowed, count, unknown):
    company = CompanyFeatures(
        company_id="example",
        rechtsform=form,
        features=[feature(settings)],
    )
    result = filter_pairs(company, {1: law(legal_forms=allowed)}, [section(settings)])
    assert len(result.pairs) == count
    assert result.removed == {"legal_form": 1 - count, "tax_type": 0}
    assert result.form_unknown == ({1} if unknown else set())


def test_tax_type_filter_and_norm_signal(settings):
    company = CompanyFeatures(
        company_id="example",
        features=[
            feature(settings, 1, steuerarten=["KSt"], normen=["§ 8c KStG"]),
            feature(settings, 2, steuerarten=["USt"]),
            feature(settings, 3),
        ],
    )
    sections = [section(settings, tax_types=["KSt"], norms=["§ 8c KStG"]), section(settings, 2)]
    result = filter_pairs(company, {1: law()}, sections)
    assert result.removed == {"legal_form": 0, "tax_type": 1}
    assert [(p.feature.objekt_id, p.section.nr) for p in result.pairs] == [
        (1, 1),
        (3, 1),
        (1, 2),
        (2, 2),
        (3, 2),
    ]
    assert [p.norm_match for p in result.pairs] == [True, False, False, False, False]


def test_max_not_average_and_best_three(settings):
    vectors = [[1, 0], [0, 1], [-1, 0], [0.6, 0.8]]
    pairs = [Pair(feature(settings, n, v), section(settings)) for n, v in enumerate(vectors, 1)]
    (scored,) = rank_sections(pairs, {1: law()}, set(), settings)
    assert sum(cosine(v, [1, 0]) for v in vectors) / len(vectors) < 0.4
    assert scored.passed and scored.score == 1
    assert [s.feature.objekt_id for s in scored.features] == [1, 4, 2]


def test_threshold_across_laws_and_norm_tie_break(settings):
    """The cap no longer limits stage 2: it is the judge budget per execution (below)."""
    settings = settings.model_copy(update={"match_score_threshold": 0.6, "match_max_candidates": 2})
    scores = [0.5, 0.6, 0.8, 0.8]
    sections = [section(settings, n, [s, math.sqrt(1 - s * s)]) for n, s in enumerate(scores, 1)]
    sections[-1].law_id = 2
    laws = {1: law(), 2: law().model_copy(update={"id": 2})}
    pairs = [Pair(feature(settings), s, s.nr == 4) for s in sections]
    ranked = rank_sections(pairs, laws, {2}, settings)
    assert [s.section.nr for s in ranked] == [4, 3, 2, 1]
    assert [s.passed for s in ranked] == [True, True, True, False]
    assert ranked[0].form_unknown
    # Norm overlap alone cannot lift a section below the threshold.
    (below,) = rank_sections([Pair(feature(settings), sections[0], True)], laws, set(), settings)
    assert below.norm_match and not below.passed


async def test_dry_run_has_no_calls_or_writes(settings, project):
    repo = await ready(settings, project)
    before = (await repo.get_project_state(project)).model_dump()
    client = FakeClient()
    (report,) = await Screener(repo, settings, client).screen(project, dry_run=True)
    assert report.dry_run and report.candidates == 1 and report.scored[0].score == 1
    assert await repo.list_matches(project) == []
    assert (await repo.get_project_state(project)).model_dump() == before
    assert client.completions.calls == client.embeddings.calls == 0
    assert (await Screener(repo, settings, None).screen(project, dry_run=True))[0].candidates == 1


@pytest.mark.parametrize("kind", ["dimension", "model"])
async def test_incompatible_vectors_refused_and_lock_released(settings, project, kind):
    repo = await ready(settings, project)
    stored = next(iter(repo.sections.values()))
    if kind == "dimension":
        stored.vector = [1, 0, 0]
    else:
        stored.model = "different-model"
    client = FakeClient(text=verdict())
    with pytest.raises(
        MatchError,
        match="dimensions differ"
        if kind == "dimension"
        else f"different-model.*{settings.embedding_model}",
    ):
        await Screener(repo, settings, client).screen(project)
    assert client.completions.calls == 0
    assert await repo.list_matches(project) == []
    assert (await repo.get_project_state(project)).running_step is None


@pytest.fixture
def candidate(settings):
    pairs = [
        Pair(feature(settings, n, v), section(settings))
        for n, v in enumerate([[1, 0], [0.9, 0.1], [0.8, 0.2]], 1)
    ]
    return rank_sections(pairs, {1: law()}, set(), settings)[0]


@pytest.mark.parametrize(
    "answers,expected,calls",
    [
        ([verdict()], 1, 1),
        ([verdict(False), verdict()], 2, 2),
        ([verdict(False, "Beste Ablehnung."), verdict(False), verdict(False)], 1, 3),
    ],
)
async def test_judge_stops_at_first_yes_or_keeps_best_no(
    settings,
    candidate,
    answers,
    expected,
    calls,
):
    client = FakeClient(responses=answers)
    outcome = await Judge(settings, client, asyncio.Semaphore(1)).judge_section(candidate)
    assert not outcome.unjudged and outcome.pair.feature.objekt_id == expected
    assert outcome.verdict.relevant == (calls != 3)
    if calls == 3:
        assert outcome.verdict.reason == "Beste Ablehnung."
    assert client.completions.calls == calls


@pytest.mark.parametrize(
    "bad",
    [
        "not json",
        verdict(reason=" "),
        Reply(None, refusal="refused"),
        Reply("{", finish_reason="length"),
    ],
)
async def test_judge_repairs_once(settings, candidate, bad):
    client = FakeClient(responses=[bad, verdict()])
    outcome = await Judge(settings, client, asyncio.Semaphore(1)).judge_section(candidate)
    assert outcome.verdict.relevant and not outcome.unjudged
    assert client.completions.calls == 2
    assert client.completions.requests[0]["response_format"]["json_schema"]["strict"]


async def test_persistently_invalid_is_unjudged_and_retried(settings, project):
    repo = await ready(settings, project)
    client = FakeClient(text=verdict(reason=""))
    screener = Screener(repo, settings, client)
    (report,) = await screener.screen(project)
    assert report.unjudged == ["Rz. 1"] and report.stored == 0
    assert client.completions.calls == 2 and await repo.list_matches(project) == []
    client.completions.text = verdict()
    (retried,) = await screener.screen(project)
    assert retried.hits == 1 and not retried.unjudged and client.completions.calls == 3


async def test_failed_pair_does_not_become_a_no_or_stop_other_sections(settings, project):
    repo = await ready(settings, project, sections=[section(settings), section(settings, 2)])
    client = FakeClient(
        responder=lambda kw: (
            "invalid" if "Abschnitt: Rz. 1\n" in kw["messages"][1]["content"] else verdict()
        )
    )
    (report,) = await Screener(repo, settings, client).screen(project)
    assert report.unjudged == ["Rz. 1"] and report.hits == report.stored == 1
    assert [m.section_ref for m in await repo.list_matches(project)] == ["Rz. 2"]


async def test_invalid_pair_then_no_keeps_section_unjudged(settings, candidate):
    client = FakeClient(responses=["invalid", "invalid", verdict(False), verdict(False)])
    outcome = await Judge(settings, client, asyncio.Semaphore(1)).judge_section(candidate)
    assert outcome.unjudged and outcome.verdict is None
    assert client.completions.calls == 4


def test_judge_input_uses_sentence_metadata_and_law_only(candidate):
    f = candidate.features[0].feature
    f.jahre, f.offen = (2022, 2023), True
    f.normen, f.steuerarten = ["§ 8c KStG"], ["KSt"]
    candidate.form_unknown = candidate.features[0].norm_match = True
    message = judge_message(candidate, candidate.features[0])
    assert f"Sachverhalt: {f.text_basis}" in message
    assert "Jahre: 2022 bis 2023; noch änderbar: ja" in message
    assert "Normtreffer: ja" in message and "Rechtsform unbekannt: ja" in message
    assert "Nordhafen" not in message and "2412380" not in message
    assert "company_id" not in message and "gesellschaft_id" not in message


async def test_rerun_changed_sentence_removed_feature_and_force(settings, project):
    # One feature per section, so a changed sentence changes one section's judge inputs.
    settings = settings.model_copy(update={"judge_features_per_section": 1})
    repo = await ready(
        settings,
        project,
        features=[feature(settings), feature(settings, 2, [0, 1])],
        sections=[section(settings), section(settings, 2, [0, 1])],
    )
    client = FakeClient(text=verdict())
    screener = Screener(repo, settings, client)
    assert (await screener.screen(project))[0].stored == 2
    original_ids = {m.id for m in await repo.list_matches(project)}
    assert (await screener.screen(project))[0].skipped == 2 and client.completions.calls == 2
    repo.embeddings[project][0].text_basis = "Die Gesellschaft hat gewerbliche Verlustvorträge."
    repo.states[project].profile_version = 2
    changed = (await screener.screen(project))[0]
    assert changed.stored == changed.skipped == 1 and client.completions.calls == 3
    assert {m.id for m in await repo.list_matches(project)} == original_ids
    updated = next(m for m in await repo.list_matches(project) if m.objekt_id == 1)
    assert (
        updated.profile_version == 2
        and updated.feature_text == repo.embeddings[project][0].text_basis
    )
    assert (await screener.screen(project, force=True))[0].stored == 2
    assert client.completions.calls == 5
    # Replace an object key while keeping its sentence: the old key no longer exists.
    repo.embeddings[project][0].objekt_id = 3
    assert (await screener.screen(project))[0].stored == 1 and client.completions.calls == 6
    assert len(await repo.list_matches(project, only_hits=True)) == 2


async def test_screen_pending_marks_only_completed_company_changes(settings, project):
    repo = await ready(settings, project)
    repo.audit = [
        AuditEntry(id=n, kind=kind, company_id=cid, acted_by="advisor")
        for n, kind, cid in [
            (1, AuditKind.PROFILE_CHANGED, project),
            (2, AuditKind.PROFILE_CHANGED, "other"),
            (3, AuditKind.PROFILE_CONFIRMED, project),
        ]
    ]
    client = FakeClient(text="invalid")
    screener = Screener(repo, settings, client)
    assert (await screener.screen_pending(project)).unjudged
    assert await repo.pending_profile_changes(project) == [1]
    client.completions.text = verdict()
    report = await screener.screen_pending(project)
    assert report.trigger is Trigger.PROFIL_UPDATE and report.stored == 1
    assert repo.audit[0].screened_at is not None
    assert all(e.screened_at is None for e in repo.audit[1:])
    assert (await repo.list_matches(project))[0].trigger is Trigger.PROFIL_UPDATE
    calls = client.completions.calls
    assert (await screener.screen_pending(project)).status == "nothing_to_do"
    repo.audit.append(
        AuditEntry(id=4, kind=AuditKind.PROFILE_CHANGED, company_id=project, acted_by="advisor")
    )
    assert (await screener.screen_pending(project)).skipped == 1
    assert client.completions.calls == calls and repo.audit[-1].screened_at is not None


@pytest.mark.parametrize("state", [None, ProjectState.PUBLISHED, ProjectState.CONFIRMED])
async def test_refused_states(settings, project, state):
    repo = await ready(settings, project)
    if state is None:
        del repo.states[project]
    else:
        repo.states[project].state = state
    client = FakeClient()
    with pytest.raises(StateError, match=str(state) if state else "unknown"):
        await Screener(repo, settings, client).screen(project)
    assert client.completions.calls == 0


async def test_stale_busy_and_all_company_screening(settings, project):
    repo = await ready(settings, project)
    client = FakeClient(text=verdict())
    screener = Screener(repo, settings, client)
    repo.states[project].embeddings_stale = True
    with pytest.raises(StateError, match="stale.*--advance"):
        await screener.screen(project)
    repo.states[project].embeddings_stale = False
    await repo.begin_step(project, "update", settings.step_lock_timeout_s)
    with pytest.raises(BusyError, match="update"):
        await screener.screen(project)
    assert repo.states[project].running_step == "update"
    await repo.end_step(project)
    repo.states[project].state = ProjectState.CLEANED
    repo.states["unconfirmed"] = ProjectStatus(company_id="unconfirmed", state="published")
    reports = await screener.screen()
    assert {r.company_id: r.status for r in reports} == {
        project: "screened",
        "unconfirmed": "refused",
    }
    assert client.completions.calls == 1


async def test_postgres_ingest_dry_run_screen_and_listing(
    pg_repository,
    profil_schema,
    settings,
    project,
    tmp_path,
):
    """Nordhafen-style profile through publish, confirm, fake embed, ingest and screen.

    Unique company and PDF keep existing seed/live rows intact.
    """
    repo = pg_repository
    view = await published(repo, project, profil_schema, settings)
    await confirm_profile(repo, profil_schema, settings, decide_all(view))
    embed_client = FakeClient(text="Die Gesellschaft hat steuerliche Verlustvorträge.")
    await Embedder(repo, profil_schema, settings, embed_client).run(project)
    ingested = await ingest_law(
        law_pdf(tmp_path, f"{project}.pdf"), repo, settings, FakeClient(text=json.dumps(METADATA))
    )
    try:
        company = await repo.company_features(project)
        assert company.rechtsform == "GmbH" and len(company.features) == 3
        assert all(f.modell == settings.embedding_model for f in company.features)
        client = FakeClient(text=verdict())
        screener = Screener(repo, settings, client)
        (dry,) = await screener.screen(project, ingested.law_id, dry_run=True)
        assert dry.candidates > 0 and client.completions.calls == 0
        assert await repo.list_matches(project) == []
        (screened,) = await screener.screen(project, ingested.law_id)
        hits = await repo.list_matches(project, only_hits=True)
        assert screened.hits == len(hits) == dry.candidates
        source = next(law for law in await repo.list_laws() if law.id == ingested.law_id)
        assert source.change_summary == METADATA["change_summary"]
        # The additive migration and metadata refresh preserve already-judged matches.
        await repo.initialize()
        await repo.update_law_change_summary(
            ingested.law_id, "Das Gericht klärt die Verlustnutzung."
        )
        assert await repo.list_matches(project, only_hits=True) == hits
        source = next(law for law in await repo.list_laws() if law.id == ingested.law_id)
        assert source.change_summary == "Das Gericht klärt die Verlustnutzung."
        assert all(
            m.law_title == METADATA["title"]
            and m.section_ref
            and m.judge_reason
            and m.profile_version == 1
            for m in hits
        )
        calls = client.completions.calls
        assert (await screener.screen(project, ingested.law_id))[0].skipped == len(hits)
        assert client.completions.calls == calls
        client.completions.text = verdict(False)
        await screener.screen(project, ingested.law_id, force=True)
        assert await repo.list_matches(project, only_hits=True) == []
        assert len(await repo.list_matches(project)) == len(hits)
    finally:
        async with await psycopg.AsyncConnection.connect(os.environ["TEST_DATABASE_URL"]) as conn:
            await conn.execute("DELETE FROM screening.law WHERE id = %s", (ingested.law_id,))


async def test_seeded_nordhafen_screening_postgres(
    pg_repository,
    profil_schema,
    settings,
    tmp_path,
):
    """The plan's seed-specific check, using its real features and fake model calls."""
    from app.config import PROFILE_DB_DIR

    repo = pg_repository
    await repo.initialize_profile_schema(PROFILE_DB_DIR, seed=True)
    project = "Hafenkante"
    # A live project state belongs to its owner; the isolated test above covers that case.
    if await repo.get_project_state(project) is not None:
        pytest.skip("seed has an existing project state; leave it intact")
    model = f"screen-test-{uuid.uuid4().hex}"
    config = settings.model_copy(update={"embedding_model": model})
    law_id = None
    async with await psycopg.AsyncConnection.connect(os.environ["TEST_DATABASE_URL"]) as conn:
        await conn.execute(
            "INSERT INTO pipeline.project_state (company_id,state) VALUES (%s,'confirmed')",
            (project,),
        )
    try:
        embedded = await Embedder(
            repo,
            profil_schema,
            config,
            FakeClient(text="Die Gesellschaft hat steuerliche Verlustvorträge."),
        ).run(project)
        assert embedded.written > 3  # Full seed, rather than the compact fixture above.
        ingested = await ingest_law(
            law_pdf(tmp_path, f"{model}.pdf"),
            repo,
            config,
            FakeClient(text=json.dumps(METADATA)),
        )
        law_id = ingested.law_id
        client = FakeClient(text=verdict())
        screener = Screener(repo, config, client)
        (dry,) = await screener.screen(project, law_id, dry_run=True)
        assert dry.candidates > 0 and client.completions.calls == 0
        (judged,) = await screener.screen(project, law_id)
        hits = [m for m in await repo.list_matches(project, only_hits=True) if m.law_id == law_id]
        assert len(hits) == judged.hits == dry.candidates
        calls = client.completions.calls
        assert (await screener.screen(project, law_id))[0].skipped == len(hits)
        assert client.completions.calls == calls
    finally:
        async with await psycopg.AsyncConnection.connect(os.environ["TEST_DATABASE_URL"]) as conn:
            if law_id is not None:  # the seed project keeps no alert of this test law
                await conn.execute(
                    "DELETE FROM screening.alert WHERE company_id = %s "
                    "AND (source_snapshot->'law'->>'id')::bigint = %s",
                    (project, law_id),
                )
                await conn.execute("DELETE FROM screening.law WHERE id = %s", (law_id,))
            await conn.execute("DELETE FROM profil.embedding WHERE modell = %s", (model,))
            await conn.execute(
                "DELETE FROM pipeline.project_state WHERE company_id = %s", (project,)
            )
