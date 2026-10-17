"""A small profile for the real schema (`profil_schema.json`): candidates, extraction records
and the merged draft, shared by the writer, confirm, embed and cleanup tests. Evidence
quotes start with QUOTE so a test can prove they never reach schema `profil`."""

import psycopg

from app.models.extraction import Candidate, DocumentExtraction
from app.models.profile import DecisionAction
from app.models.review import CellChoice, ConfirmRequest, Decision, ReviewView
from app.profile.merge import merge_candidates

QUOTE = "BELEGZITAT"
DOCS = {  # doc_id -> (filename, klasse_code)
    "report": ("01_Tax-DD-Report.pdf", "dd_report"),
    "spa": ("02_SPA-Auszug.pdf", "spa"),
    "register": ("03_Handelsregisterauszug.pdf", "register"),
    "notice": ("08_Feststellungsbescheide.pdf", "bescheid"),
    "draft": ("06_Gewinnabfuehrungsvertrag_ENTWURF.pdf", "entwurf"),
}


def cand(project, field, value, doc, page=1, confidence=0.9):
    return Candidate(
        company_id=project,
        field_name=field,
        value=value,
        evidence=f"{QUOTE} {field} {doc}",
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


def merkmal(project, code, wert, kurztext, doc="report"):
    return cand(project, "merkmal", {"merkmal_code": code, "wert": wert, "kurztext": kurztext}, doc)


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
        merkmal(project, "B04", "nein", "GAV nur Entwurf", "register"),
        merkmal(project, "B07", "ja", "Tochter Nordhafen Logistics B.V. (NL), 100 %"),
        merkmal(project, "C09", "unbekannt", "Prüfung offen"),
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
                "titel": "Verlustuntergang durch Closing 2026",
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


def scalar_conflict(project):
    """A second purchase price: a scalar conflict (beyond rounding)."""
    return cand(project, "kaufpreis_eur", 38_000_000, "report", page=3)


def with_conflict(project):
    return candidates(project) + [scalar_conflict(project)]


def draft_for(project, profil_schema, cands=None):
    return merge_candidates(
        cands if cands is not None else candidates(project),
        profil_schema,
        company_id=project,
        max_granularity=1000,
        extractions=extractions(project),
    )


_GES = (
    "(SELECT g.id FROM profil.gesellschaft g JOIN profil.deal d ON d.id = g.deal_id "
    "WHERE d.projektname = %(p)s)"
)
_DEAL = "(SELECT id FROM profil.deal WHERE projektname = %(p)s)"
# How the rows of each fact table belong to a project (default: gesellschaft_id).
_ROW_FILTERS = {
    "gesellschaft": f"t.id IN {_GES}",
    "organschaft": f"t.organgesellschaft_id IN {_GES}",
    "darlehen": f"t.darlehensnehmer_id IN {_GES}",
    "grundstueck": f"t.nutzer_id IN {_GES}",
    "finding": f"t.deal_id IN {_DEAL}",
    "spa_absicherung": f"t.deal_id IN {_DEAL}",
    "pruefungsfeststellung": "t.aussenpruefung_id IN (SELECT id FROM profil.aussenpruefung "
    f"WHERE gesellschaft_id IN {_GES})",
}
# Rows without provenance columns that still belong to the project.
OTHER_FILTERS = {
    "deal": "t.projektname = %(p)s",
    "dokument": f"t.deal_id IN {_DEAL}",
    "pruefhinweis": f"t.gesellschaft_id IN {_GES}",
    "embedding": f"t.gesellschaft_id IN {_GES}",
}


async def fact_rows(url: str, project: str) -> list[tuple]:
    """(table, row as text, bestaetigt_von, bestaetigt_am) of every fact row of a project."""
    async with await psycopg.AsyncConnection.connect(url) as conn:
        cur = await conn.execute(
            "SELECT table_name FROM information_schema.columns WHERE table_schema = 'profil' "
            "AND column_name = 'bestaetigt_von' ORDER BY 1"
        )
        rows = []
        for (table,) in await cur.fetchall():
            where = _ROW_FILTERS.get(table, f"t.gesellschaft_id IN {_GES}")
            found = await conn.execute(
                f"SELECT t::text, t.bestaetigt_von, t.bestaetigt_am FROM profil.{table} t "
                f"WHERE {where}",
                {"p": project},
            )
            rows += [(table, *row) for row in await found.fetchall()]
        return rows


async def all_profile_text(url: str, project: str) -> list[str]:
    """Every row of the project in schema profil as text (for the quote scan)."""
    texts = [text for _, text, _, _ in await fact_rows(url, project)]
    async with await psycopg.AsyncConnection.connect(url) as conn:
        for table, where in OTHER_FILTERS.items():
            cur = await conn.execute(
                f"SELECT t::text FROM profil.{table} t WHERE {where}", {"p": project}
            )
            texts += [row[0] for row in await cur.fetchall()]
    return texts


def decide_all(view: ReviewView, confirmed_by="Dr. Test") -> ConfirmRequest:
    """A complete decisions file: group 0 for conflicts, leave empty for missing fields."""
    decisions = []
    for open_ in view.open_decisions:
        if open_.kind == "conflict":
            decisions.append(Decision(field=open_.field, action=DecisionAction.CHOOSE, group=0))
        elif open_.kind == "missing":
            decisions.append(Decision(field=open_.field, action=DecisionAction.LEAVE_EMPTY))
        else:
            cells = {name: CellChoice(group=0) for name in open_.cells}
            decisions.append(
                Decision(field=open_.field, key=open_.key, action=DecisionAction.EDIT, cells=cells)
            )
    return ConfirmRequest(
        company_id=view.company_id,
        draft_version=view.draft_version,
        confirmed_by=confirmed_by,
        decisions=decisions,
    )
