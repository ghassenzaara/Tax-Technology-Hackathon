"""Step 9: feature selection, sentences (validation, repair, fallback), embedding call,
metadata and the completeness record. FakeClient only; Postgres part skipped without
TEST_DATABASE_URL."""

import os
import re
import uuid

import openai
import psycopg
import pytest

from app.db.memory import MemoryRepository
from app.db.repository import StateError
from app.features.select import forbidden_terms, offen_for, select_features
from app.features.sentences import fallback_sentence, sentence_violations
from app.models.features import AssessmentRow, Feature, ProfileSnapshot
from app.models.project import ProjectState
from app.services.confirm import confirm_profile
from app.services.embed import Embedder, EmbedError
from tests.helpers import FakeClient, api_error
from tests.profil_data import candidates, decide_all, merkmal
from tests.test_confirm import published

GOOD = "Die Gesellschaft ist an einer ausländischen Tochtergesellschaft beteiligt."


def chat(answer):
    """Responder for sentence calls: a fixed answer, or answer(user_message)."""
    return lambda kwargs: answer(kwargs["messages"][1]["content"]) if callable(answer) else answer


async def confirmed(project, profil_schema, settings, cands=None):
    """A memory project in state confirmed (steps 7 and 8 done)."""
    repository = MemoryRepository()
    cands = cands if cands is not None else candidates(project)
    view = await published(repository, project, profil_schema, settings, cands)
    await confirm_profile(repository, profil_schema, settings, decide_all(view))
    return repository


def embedder(repository, profil_schema, settings, client):
    return Embedder(repository, profil_schema, settings, client)


async def test_selection_metadata_and_state(profil_schema, settings, project):
    repository = await confirmed(project, profil_schema, settings)
    client = FakeClient(responder=chat(GOOD))

    report = await embedder(repository, profil_schema, settings, client).run(project)

    records = {r.objekt_typ: r for r in repository.embeddings[project]}
    assert sorted(records) == ["finding", "merkmal_wert", "rechtsbehelf"]  # B07 ja only
    assert report.expected == report.written == 3 and report.fallback == 0
    assert report.headline().startswith("3 features embedded in")
    finding = records["finding"]
    assert finding.steuerarten == ["GewSt", "KSt"]
    assert finding.normen == ["§ 8c KStG", "§ 10a GewStG"]
    assert finding.jahre == (2026, None) and finding.offen is True
    appeal = records["rechtsbehelf"]
    assert (appeal.steuerarten, appeal.jahre, appeal.offen) == (["GewSt"], (2022, 2022), True)
    assert records["merkmal_wert"].jahre is None and records["merkmal_wert"].offen is None
    assert all(r.text_basis == GOOD and len(r.vektor) == 4 for r in records.values())
    state = await repository.get_project_state(project)
    assert state.state is ProjectState.EMBEDDED and state.embeddings_written == 3
    assert state.expected_features == sorted(r.ref for r in records.values())
    assert client.embeddings.calls == 1  # one batch


async def test_nein_and_unbekannt_merkmale_are_not_embedded(profil_schema, settings, project):
    cands = [c for c in candidates(project) if c.field_name in ("deal_typ", "kaeufer", "name")]
    cands += [c for c in candidates(project) if c.field_name in ("rechtsform", "rolle")]
    cands += [
        merkmal(project, "B04", "nein", "kein GAV"),
        merkmal(project, "C09", "unbekannt", "offen"),
        merkmal(project, "E05", "ja", "Grundbesitz"),
    ]
    repository = await confirmed(project, profil_schema, settings, cands)

    await embedder(repository, profil_schema, settings, FakeClient(responder=chat(GOOD))).run(
        project
    )

    (record,) = repository.embeddings[project]
    assert record.objekt_typ == "merkmal_wert"


async def test_digits_trigger_repair_then_fallback(profil_schema, settings, project):
    repository = await confirmed(project, profil_schema, settings)
    client = FakeClient(responder=chat("Im Jahr 2026 entfällt der Verlustvortrag nach § 8c KStG."))

    report = await embedder(repository, profil_schema, settings, client).run(project)

    assert client.completions.calls == 6  # three features, one repair each
    repair = client.completions.requests[-1]["messages"][-1]["content"]
    assert "digits" in repair
    assert report.fallback == 3 and len(report.fallback_features) == 3
    texts = [r.text_basis for r in repository.embeddings[project]]
    assert all(not re.search(r"\d", t) for t in texts)
    finding = next(r for r in repository.embeddings[project] if r.objekt_typ == "finding")
    assert (
        finding.text_basis
        == "Steuerliches Risiko der Gesellschaft: Verlustuntergang durch Closing."
    )
    state = await repository.get_project_state(project)
    assert state.embeddings_fallback == 3


async def test_names_are_rejected_and_never_embedded(profil_schema, settings, project):
    repository = await confirmed(project, profil_schema, settings)

    def answer(message):  # names from the profile, as a careless model might write
        return "Die Weserlinie erwirbt Anteile; Henrik Albers gab ein Darlehen."

    report = await embedder(
        repository, profil_schema, settings, FakeClient(responder=chat(answer))
    ).run(project)

    assert report.fallback == 3
    for record in repository.embeddings[project]:
        for name in ("Weserlinie", "Albers", "Testfirma", "Nordhafen"):
            assert name.lower() not in record.text_basis.lower()


async def test_repair_succeeds_on_second_answer(profil_schema, settings, project):
    repository = await confirmed(project, profil_schema, settings)
    # First answer has a year; the repair request (four messages) gets a valid sentence.
    client = FakeClient(
        responder=lambda kwargs: GOOD if len(kwargs["messages"]) > 2 else "Seit 2020 gilt das."
    )

    report = await embedder(repository, profil_schema, settings, client).run(project)

    assert report.fallback == 0 and client.completions.calls == 6


@pytest.mark.parametrize(
    "client",
    [
        FakeClient(responder=chat(GOOD), embedding_dim=3),  # wrong dimension
        FakeClient(responder=chat(GOOD), vectors=lambda texts: [[1.0] * 4 for _ in texts[1:]]),
    ],
    ids=["dimension", "missing-vector"],
)
async def test_bad_vectors_fail_without_state_change(profil_schema, settings, project, client):
    repository = await confirmed(project, profil_schema, settings)

    with pytest.raises(EmbedError):
        await embedder(repository, profil_schema, settings, client).run(project)

    state = await repository.get_project_state(project)
    assert state.state is ProjectState.CONFIRMED and "embed failed" in state.last_error
    assert project not in repository.embeddings


async def test_rate_limit_is_retried(profil_schema, settings, project):
    repository = await confirmed(project, profil_schema, settings)
    client = FakeClient(
        responder=chat(GOOD), embedding_errors=[api_error(openai.RateLimitError, 429)]
    )

    report = await embedder(repository, profil_schema, settings, client).run(project)

    assert report.written == 3 and client.embeddings.calls == 2


async def test_rerun_replaces_vectors(profil_schema, settings, project):
    repository = await confirmed(project, profil_schema, settings)
    run = embedder(repository, profil_schema, settings, FakeClient(responder=chat(GOOD)))

    await run.run(project)
    await run.run(project)  # embedded -> embedded

    refs = [r.ref for r in repository.embeddings[project]]
    assert len(refs) == len(set(refs)) == 3


async def test_embed_needs_a_confirmed_profile(profil_schema, settings, project):
    repository = MemoryRepository()
    await published(repository, project, profil_schema, settings)

    with pytest.raises(StateError, match="confirmed profile"):
        await embedder(repository, profil_schema, settings, FakeClient()).run(project)


def test_sentence_rules(settings):
    feature = Feature(
        objekt_typ="finding",
        objekt_id=1,
        gesellschaft_id=1,
        facts={},
        fallback="Steuerliches Risiko der Gesellschaft: Verlustuntergang " + "sehr " * 50,
        forbidden=forbidden_terms(["Nordhafen Logistics B.V.", "07_Steuerbescheide.pdf"]),
    )

    assert sentence_violations(GOOD, feature, settings) == []
    assert sentence_violations("", feature, settings) == ["the answer is empty"]
    assert "digits" in sentence_violations("Im VZ 2022.", feature, settings)[0]
    assert "names" in sentence_violations("Die nordhafen ist beteiligt.", feature, settings)[0]
    assert sentence_violations("Ein Steuerbescheide-Text ohne Zahl.", feature, settings) == []
    fallback = fallback_sentence(feature, settings)
    assert len(fallback.split()) == settings.sentence_max_words
    assert sentence_violations(fallback, feature, settings) == []


def test_forbidden_terms_keep_generic_words():
    terms = forbidden_terms(
        ["Weserlinie Logistics BidCo GmbH", "Henrik Albers", "01_Tax-DD-Report.pdf"]
    )

    assert {"Weserlinie", "BidCo", "Henrik", "Albers"} <= set(terms)
    assert "Logistics" not in terms and "GmbH" not in terms and "Report" not in terms


def test_offen_follows_open_years():
    open_2022 = AssessmentRow(jahr=2022, steuerart="GewSt", stand="veranlagt", vorbehalt_164="ja")
    closed_2021 = AssessmentRow(jahr=2021, steuerart="KSt", stand="veranlagt", vorbehalt_164="nein")
    rows = [open_2022, closed_2021]

    assert offen_for((2022, 2022), ["GewSt"], rows) is True
    assert offen_for((2021, 2021), ["KSt"], rows) is False
    assert offen_for((2023, 2024), ["KSt"], rows) is True  # not assessed yet
    assert offen_for((2026, None), [], rows) is True  # open-ended range
    assert offen_for(None, [], rows) is None


def test_selection_skips_other_objects(settings):
    snapshot = ProfileSnapshot.model_validate(
        {
            "company_id": "x",
            "gesellschaft_id": 7,
            "merkmale": [
                {"id": 1, "code": "B07", "wert": "ja", "bezeichnung": "Auslandsbezug"},
                {"id": 2, "code": "B04", "wert": "nein"},
                {"id": 3, "code": "C09", "wert": "unbekannt"},
                {
                    "id": 4,
                    "code": "E01",
                    "wert": "ja",
                    "normen": [{"code": "§ 8 Abs. 3 KStG", "gesetz": "KStG"}],
                },
            ],
        }
    )

    features = select_features(snapshot, settings)

    assert [f.ref for f in features] == ["merkmal_wert:1", "merkmal_wert:4"]
    assert features[1].steuerarten == ["KSt"] and features[0].gesellschaft_id == 7


# Postgres: the seeded Nordhafen solution profile ---------------------------------------


async def test_embed_seeded_nordhafen_postgres(pg_repository, profil_schema, settings):
    from app.config import PROFILE_DB_DIR

    await pg_repository.initialize_profile_schema(PROFILE_DB_DIR, seed=True)
    url = os.environ["TEST_DATABASE_URL"]
    model = f"test-model-{uuid.uuid4().hex[:8]}"  # own rows only, removed afterwards
    run_settings = settings.model_copy(update={"embedding_model": model})

    async def sql(statement, *params):
        async with await psycopg.AsyncConnection.connect(url) as conn:
            cur = await conn.execute(statement, params)
            return await cur.fetchall() if cur.description else None

    await sql(
        "INSERT INTO pipeline.project_state (company_id, state) VALUES ('Hafenkante', 'confirmed')"
    )
    try:
        client = FakeClient(responder=chat(GOOD))
        report = await Embedder(pg_repository, profil_schema, run_settings, client).run(
            "Hafenkante"
        )

        (expected,) = await sql(
            "SELECT (SELECT count(*) FROM profil.merkmal_wert WHERE wert = 'ja' "
            "AND gesellschaft_id = profil.ges('Nordhafen Logistik GmbH')) "
            "+ (SELECT count(*) FROM profil.finding WHERE deal_id = profil.deal_id('Hafenkante')) "
            "+ (SELECT count(*) FROM profil.rechtsbehelf "
            "WHERE gesellschaft_id = profil.ges('Nordhafen Logistik GmbH'))"
        )
        assert report.written == expected[0] > 0
        rows = await sql(
            "SELECT objekt_typ, steuerarten::text[], normen, jahre IS NOT NULL, offen, "
            "array_length(vektor, 1), text_basis FROM profil.embedding WHERE modell = %s",
            model,
        )
        assert len(rows) == report.written
        findings = [r for r in rows if r[0] == "finding"]
        assert findings and all(r[1] and r[2] and r[3] and r[4] is not None for r in findings)
        appeals = [r for r in rows if r[0] == "rechtsbehelf"]
        assert appeals and all(r[1] == ["GewSt"] and r[3] for r in appeals)
        assert all(r[5] == 4 and not re.search(r"\d", r[6]) for r in rows)
        (state,) = await sql(
            "SELECT state, embeddings_written FROM pipeline.project_state "
            "WHERE company_id = 'Hafenkante'"
        )
        assert state == ("embedded", report.written)

        # The prompt input names the company; the stored sentence never does.
        names = (await pg_repository.load_profile_snapshot("Hafenkante", profil_schema)).names
        assert "Nordhafen Logistik GmbH" in names
    finally:
        await sql("DELETE FROM profil.embedding WHERE modell = %s", model)
        await sql("DELETE FROM pipeline.project_state WHERE company_id = 'Hafenkante'")
