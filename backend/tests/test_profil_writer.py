"""Publishing a draft into the teammate's schema `profil`. Needs Postgres:
`TEST_DATABASE_URL=postgresql://dd:dd@localhost:5432/dd` after `docker compose up -d`.
The profil schema is created when missing (never reset), so seeds stay intact."""

import os

import psycopg
import pytest

from app.config import PROFILE_DB_DIR
from app.db.repository import PublishError, StateError
from tests.profil_data import DOCS, QUOTE, cand, candidates, draft_for, extractions

DATABASE_URL = os.environ.get("TEST_DATABASE_URL")
pytestmark = pytest.mark.skipif(not DATABASE_URL, reason="TEST_DATABASE_URL not set")


@pytest.fixture
def repository(pg_repository):
    return pg_repository


async def query(sql, *params):
    async with await psycopg.AsyncConnection.connect(DATABASE_URL) as conn:
        cur = await conn.execute(sql, params)
        return await cur.fetchall()


async def test_publish_writes_profile_rows(repository, project, profil_schema, settings):
    report = await repository.publish_profile(
        draft_for(project, profil_schema), extractions(project), profil_schema, settings
    )

    assert report.rows["deal"] == 1 and report.rows["dokument"] == len(DOCS)
    assert report.rows["gesellschaft"] == 1
    (ges,) = await query(
        "SELECT g.id, g.rechtsform, g.rolle::text, g.konfidenz::text, d.dateiname "
        "FROM profil.gesellschaft g JOIN profil.deal x ON x.id = g.deal_id "
        "LEFT JOIN profil.dokument d ON d.id = g.quelle_dok WHERE x.projektname = %s",
        project,
    )
    ges_id = ges[0]
    assert ges[1:] == ("GmbH", "operativ", "hoch", "03_Handelsregisterauszug.pdf")

    (attr,) = await query(
        "SELECT betrag_eur, konfidenz::text FROM profil.steuerattribut WHERE gesellschaft_id = %s",
        ges_id,
    )
    assert attr == (2_412_380, "hoch")  # the notice wins the conflict

    hints = await query(
        "SELECT art, feld, status FROM profil.pruefhinweis WHERE gesellschaft_id = %s "
        "ORDER BY art, feld",
        ges_id,
    )
    conflict = "steuerattribut [art=kst_verlustvortrag, stichtag=2024-12-31]"
    assert ("konflikt", conflict, "offen") in hints
    assert any(art == "plausibilitaet" and feld.startswith("darlehen") for art, feld, _ in hints)
    assert report.skipped and "betrag_eur" in report.skipped[0]

    merkmale = dict(
        await query(
            "SELECT merkmal_code, wert::text FROM profil.merkmal_wert WHERE gesellschaft_id = %s",
            ges_id,
        )
    )
    assert merkmale["B04"] == "nein"
    assert merkmale["C09"] == "unbekannt"  # silence is unbekannt, never nein
    assert len(merkmale) == 11

    (organschaft,) = await query(
        "SELECT status, organgesellschaft_name, konfidenz::text FROM profil.organschaft "
        "WHERE organgesellschaft_id = %s",
        ges_id,
    )
    assert organschaft == ("geplant", f"Testfirma {project} GmbH", "niedrig")

    (rechtsbehelf,) = await query(
        "SELECT r.musterverfahren_az, v.jahr FROM profil.rechtsbehelf r "
        "JOIN profil.veranlagung v ON v.id = r.veranlagung_id WHERE r.gesellschaft_id = %s",
        ges_id,
    )
    assert rechtsbehelf == ("IV R 88/24", 2022)

    norms = await query(
        "SELECT n.norm_code FROM profil.finding_norm n JOIN profil.finding f "
        "ON f.id = n.finding_id JOIN profil.deal d ON d.id = f.deal_id "
        "WHERE d.projektname = %s ORDER BY 1",
        project,
    )
    assert norms == [("§ 10a GewStG",), ("§ 8c KStG",)]

    # The views of the teammate's schema see the published rows.
    az = await query(
        "SELECT aktenzeichen FROM profil.v_aktenzeichen_im_profil WHERE gesellschaft_id = %s",
        ges_id,
    )
    assert ("IV R 88/24",) in az


async def test_fundstelle_is_a_citation_without_quote(repository, project, profil_schema, settings):
    # Plan 2.5: filename and page only (page only when quelle_dok links the document).
    await repository.publish_profile(
        draft_for(project, profil_schema), extractions(project), profil_schema, settings
    )

    rows = await query(
        "SELECT r.fundstelle, r.quelle_dok IS NOT NULL FROM profil.rechtsbehelf r "
        "JOIN profil.gesellschaft g ON g.id = r.gesellschaft_id "
        "JOIN profil.deal d ON d.id = g.deal_id WHERE d.projektname = %s",
        project,
    )
    assert rows == [("S. 1", True)]
    texts = await query(
        "SELECT p::text FROM profil.pruefhinweis p JOIN profil.gesellschaft g "
        "ON g.id = p.gesellschaft_id JOIN profil.deal d ON d.id = g.deal_id "
        "WHERE d.projektname = %s",
        project,
    )
    assert texts and not any(QUOTE in t for (t,) in texts)
    (state,) = await query(
        "SELECT state, published_docs FROM pipeline.project_state WHERE company_id = %s", project
    )
    assert state == ("published", sorted(DOCS))


async def test_republish_replaces_previous(repository, project, profil_schema, settings):
    for _ in range(2):
        await repository.publish_profile(
            draft_for(project, profil_schema), extractions(project), profil_schema, settings
        )

    deals = await query("SELECT count(*) FROM profil.deal WHERE projektname = %s", project)
    assert deals == [(1,)]


def audit(project, von, bis, status="abgeschlossen"):
    return cand(
        project,
        "aussenpruefung",
        {
            "status": status,
            "steuerarten": ["KSt"],
            "zeitraum_von": von,
            "zeitraum_bis": bis,
            "anordnung_vom": None,
            "beginn_am": None,
            "bericht_vom": None,
            "schwerpunkte": None,
        },
        "report",
    )


def feststellung(project, tz, von=None, bis=None):
    return cand(
        project,
        "pruefungsfeststellung",
        {
            "tz": tz,
            "thema": "vGA",
            "steuerart": "KSt",
            "betrag_eur": 250_000,
            "pruefung_zeitraum_von": von,
            "pruefung_zeitraum_bis": bis,
        },
        "report",
    )


async def test_findings_attach_to_audits(repository, project, profil_schema, settings):
    # Review finding 6: with two audits, a finding without (or with a partial) period still
    # attaches: partial period match first, else the latest finished audit.
    cands = candidates(project) + [
        audit(project, 2012, 2015),
        audit(project, 2016, 2019),
        audit(project, 2024, 2025, status="angeordnet"),
        feststellung(project, "Tz. 1"),  # no period: latest finished audit (2016-2019)
        feststellung(project, "Tz. 2", von=2012),  # partial: 2012-2015
        feststellung(project, "Tz. 3", von=2016, bis=2019),  # exact
    ]

    report = await repository.publish_profile(
        draft_for(project, profil_schema, cands), extractions(project), profil_schema, settings
    )

    assert report.rows["pruefungsfeststellung"] == 3
    rows = await query(
        "SELECT f.tz, a.zeitraum_von FROM profil.pruefungsfeststellung f "
        "JOIN profil.aussenpruefung a ON a.id = f.aussenpruefung_id "
        "JOIN profil.gesellschaft g ON g.id = a.gesellschaft_id "
        "JOIN profil.deal d ON d.id = g.deal_id WHERE d.projektname = %s ORDER BY 1",
        project,
    )
    assert rows == [("Tz. 1", 2016), ("Tz. 2", 2012), ("Tz. 3", 2016)]


async def test_bad_row_value_skips_only_that_row(repository, project, profil_schema, settings):
    # Review finding 7: a value the column rejects (land is char(2)) is a clean skip with a
    # pruefhinweis, not a raw database error for the whole publish.
    bad = cand(
        project,
        "auslandsbezug",
        {
            "art": "tochtergesellschaft",
            "bezeichnung": "Nordhafen Logistics B.V.",
            "land": "Niederlande",
            "quote_prozent": 100,
            "status": "ja",
            "seit": None,
            "bemerkung": None,
        },
        "report",
    )

    report = await repository.publish_profile(
        draft_for(project, profil_schema, candidates(project) + [bad]),
        extractions(project),
        profil_schema,
        settings,
    )

    assert "auslandsbezug" not in report.rows
    assert any(s.startswith("auslandsbezug") for s in report.skipped)
    assert report.rows["gesellschaft"] == 1


async def test_unclassified_document_keeps_file_name(repository, project, profil_schema, settings):
    # Review finding 7: no document class means no dokument row; provenance still names the file.
    records = [
        e.model_copy(update={"document_class": None}) if e.doc_id == "register" else e
        for e in extractions(project)
    ]

    report = await repository.publish_profile(
        draft_for(project, profil_schema), records, profil_schema, settings
    )

    assert report.rows["dokument"] == len(DOCS) - 1
    assert any("no document class" in s for s in report.skipped)
    (fundstelle,) = await query(
        "SELECT g.fundstelle FROM profil.gesellschaft g JOIN profil.deal d ON d.id = g.deal_id "
        "WHERE d.projektname = %s",
        project,
    )
    assert fundstelle[0].startswith("03_Handelsregisterauszug.pdf S. 1")


async def test_seed_project_is_never_overwritten(repository, profil_schema, settings):
    await repository.initialize_profile_schema(PROFILE_DB_DIR, seed=True)
    seeded = draft_for("Hafenkante", profil_schema)

    with pytest.raises(
        (PublishError, StateError), match="not published by the pipeline|never overwritten"
    ):
        await repository.publish_profile(seeded, extractions("Hafenkante"), profil_schema, settings)

    (firma,) = await query(
        "SELECT g.name FROM profil.gesellschaft g JOIN profil.deal d ON d.id = g.deal_id "
        "WHERE d.projektname = 'Hafenkante'"
    )
    assert firma == ("Nordhafen Logistik GmbH",)


async def test_seed_is_repeatable(repository):
    # Review finding 4: --init-db --seed twice must not fail on duplicate keys.
    await repository.initialize_profile_schema(PROFILE_DB_DIR, seed=True)
    applied = await repository.initialize_profile_schema(PROFILE_DB_DIR, seed=True)

    assert applied == []
    assert await query("SELECT count(*) FROM profil.deal WHERE projektname = 'Hafenkante'") == [
        (1,)
    ]


async def test_missing_required_value_rolls_back(repository, project, profil_schema, settings):
    without_buyer = [c for c in candidates(project) if c.field_name != "kaeufer"]

    with pytest.raises(PublishError, match="kaeufer"):
        await repository.publish_profile(
            draft_for(project, profil_schema, without_buyer),
            extractions(project),
            profil_schema,
            settings,
        )

    assert await query("SELECT 1 FROM profil.deal WHERE projektname = %s", project) == []
