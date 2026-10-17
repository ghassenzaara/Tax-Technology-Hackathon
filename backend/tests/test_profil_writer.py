"""Publishing a draft into the teammate's schema `profil`. Needs Postgres:
`TEST_DATABASE_URL=postgresql://dd:dd@localhost:5432/dd` after `docker compose up -d`.
The profil schema is created when missing (never reset), so seeds stay intact."""

import os
import uuid

import psycopg
import pytest

from app.config import PROFILE_DB_DIR
from app.db.repository import PublishError
from app.models.extraction import Candidate, DocumentExtraction
from app.profile.merge import merge_candidates

DATABASE_URL = os.environ.get("TEST_DATABASE_URL")
pytestmark = pytest.mark.skipif(not DATABASE_URL, reason="TEST_DATABASE_URL not set")

DOCS = {  # doc_id -> (filename, klasse_code)
    "report": ("01_Tax-DD-Report.pdf", "dd_report"),
    "spa": ("02_SPA-Auszug.pdf", "spa"),
    "register": ("03_Handelsregisterauszug.pdf", "register"),
    "notice": ("08_Feststellungsbescheide.pdf", "bescheid"),
    "draft": ("06_Gewinnabfuehrungsvertrag_ENTWURF.pdf", "entwurf"),
}


@pytest.fixture
async def repository():
    from app.db.postgres import PostgresRepository

    repo = PostgresRepository(DATABASE_URL)
    await repo.initialize()
    await repo.initialize_profile_schema(PROFILE_DB_DIR)
    yield repo
    await repo.close()


@pytest.fixture
def project():
    return f"test-{uuid.uuid4().hex[:10]}"


def cand(project, field, value, doc, page=1, confidence=0.9):
    return Candidate(
        company_id=project,
        field_name=field,
        value=value,
        evidence=f"Beleg für {field}",
        source_doc=doc,
        source_filename=DOCS[doc][0],
        source_page=page,
        confidence=confidence,
    )


def extractions(project):
    return [
        DocumentExtraction(
            company_id=project,
            doc_id=doc,
            filename=filename,
            status="ok",
            document_class=klasse,
            document_title=filename,
            document_date="2026-02-03",
        )
        for doc, (filename, klasse) in DOCS.items()
    ]


def candidates(project):
    c = lambda *args, **kw: cand(project, *args, **kw)  # noqa: E731
    return [
        c("deal_typ", "share_deal", "spa"),
        c("kaeufer", "Weserlinie Logistics BidCo GmbH", "spa"),
        c("closing_am", "2026-05-12", "spa"),
        c("kaufpreis_eur", 38_400_000, "spa"),
        c("name", f"Testfirma {project} GmbH", "register"),
        c("rechtsform", "GmbH", "register"),
        c("rolle", "operativ", "report"),
        c("wz_code", "52.10", "report"),
        c(
            "merkmal",
            {"merkmal_code": "B04", "wert": "nein", "kurztext": "GAV nur Entwurf"},
            "register",
        ),
        # Conflict: rounded report figure vs exact notice; the notice (rank 1) wins.
        c(
            "steuerattribut",
            {
                "art": "kst_verlustvortrag",
                "stichtag": "2024-12-31",
                "betrag_eur": 2_412_380,
                "festgestellt": True,
                "sperre": None,
            },
            "notice",
        ),
        c(
            "steuerattribut",
            {
                "art": "kst_verlustvortrag",
                "stichtag": "2024-12-31",
                "betrag_eur": 2_600_000,
                "festgestellt": True,
                "sperre": None,
            },
            "report",
            page=7,
        ),
        c(
            "organschaft",
            {
                "art": "kst_gewst",
                "status": "geplant",
                "organtraeger_name": "Weserlinie Logistics BidCo GmbH",
                "vertrag_am": None,
                "hr_eintragung_am": None,
                "wirksam_ab": "2027-01-01",
                "mindestlaufzeit_bis": "2031-12-31",
                "bemerkung": "Entwurf",
            },
            "draft",
            confidence=0.3,
        ),
        c(
            "veranlagung",
            {
                "jahr": 2022,
                "steuerart": "GewSt",
                "bescheidart": "messbetrag",
                "stand": "veranlagt",
                "erklaerung_am": None,
                "bescheid_vom": "2024-06-21",
                "vorbehalt_164": "ja",
                "vorlaeufig_165": None,
                "vorlaeufig_punkt": None,
                "vorlaeufig_az": None,
                "abweichung_von_erklaerung": None,
                "erweiterte_kuerzung": None,
                "festsetzungsfrist_ende": "2027-12-31",
                "ablaufhemmung": "einspruch_171_3a",
                "verjaehrt": False,
            },
            "notice",
        ),
        c(
            "rechtsbehelf",
            {
                "art": "einspruch",
                "eingelegt_am": "2024-07-15",
                "status": "ruht",
                "ruht_rechtsgrundlage": None,
                "musterverfahren_gericht": "BFH",
                "musterverfahren_az": "IV R 88/24",
                "streitpunkt": "Regalanlage",
                "auswirkung_eur": 37_500,
                "veranlagung_jahr": 2022,
                "veranlagung_steuerart": "GewSt",
            },
            "report",
        ),
        c(
            "finding",
            {
                "finding_code": "K-1",
                "report_ref": "Feststellung 1",
                "titel": "Verlustuntergang",
                "steuerarten": ["KSt", "GewSt"],
                "jahr_von": 2026,
                "jahr_bis": None,
                "kurzsachverhalt": None,
                "risiko": "mittel",
                "richtung": "risiko",
                "betrag_min_eur": 0,
                "betrag_max_eur": 680_000,
                "offene_rechtsfrage": False,
                "musterverfahren_az": None,
                "status": "offen",
                "empfehlung": None,
                "normen": ["§ 8c KStG", "§ 10a GewStG"],
            },
            "report",
        ),
        # Missing a NOT NULL column (betrag_eur): skipped with a pruefhinweis.
        c(
            "darlehen",
            {
                "darlehensgeber": "Henrik Albers",
                "beziehung": "gesellschafter",
                "betrag_eur": None,
                "zinssatz_prozent": 6.0,
                "beginn_am": "2017-07-01",
                "faellig_am": None,
                "status": "zurueckgezahlt",
                "beendet_am": None,
                "abgetreten_an": None,
                "abgetreten_am": None,
            },
            "report",
        ),
    ]


def draft_for(project, profil_schema, cands=None):
    return merge_candidates(
        cands if cands is not None else candidates(project),
        profil_schema,
        company_id=project,
        max_granularity=1000,
        extractions=extractions(project),
    )


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

    with pytest.raises(PublishError, match="not published by the pipeline"):
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
