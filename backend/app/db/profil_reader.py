"""Read the confirmed profile from schema `profil` for step 9 (features to embed).

The source of truth is `profil`, never the pipeline draft: a value the advisor changed in
step 8 is read as confirmed. Names of the company, parties and documents are collected so
the sentence check can keep them out of the embedded text.
"""

from psycopg import AsyncConnection
from psycopg.rows import dict_row

from app.db.repository import RepositoryError
from app.models.features import (
    AppealRow,
    AssessmentRow,
    FindingRow,
    MerkmalRow,
    ProfileSnapshot,
)

_NORMS = (
    "coalesce(json_agg(json_build_object('code', n.code, 'gesetz', n.gesetz, "
    "'bezeichnung', n.bezeichnung) ORDER BY n.code) FILTER (WHERE n.code IS NOT NULL), "
    "'[]'::json) AS normen"
)

_MERKMALE = f"""
SELECT m.id, m.merkmal_code AS code, m.wert::text AS wert, d.bezeichnung, m.kurztext, {_NORMS}
FROM profil.merkmal_wert m
JOIN profil.merkmal_def d ON d.code = m.merkmal_code
LEFT JOIN profil.merkmal_norm mn ON mn.merkmal_code = m.merkmal_code
LEFT JOIN profil.norm n ON n.code = mn.norm_code
WHERE m.gesellschaft_id = %s
GROUP BY m.id, d.bezeichnung ORDER BY m.id
"""

_FINDINGS = f"""
SELECT f.id, f.titel, f.kurzsachverhalt, f.richtung, f.steuerarten::text[] AS steuerarten,
       f.jahr_von, f.jahr_bis, {_NORMS}
FROM profil.finding f
LEFT JOIN profil.finding_norm fn ON fn.finding_id = f.id
LEFT JOIN profil.norm n ON n.code = fn.norm_code
WHERE f.deal_id = %s
GROUP BY f.id ORDER BY f.id
"""

_APPEALS = """
SELECT r.id, r.art, r.status, r.streitpunkt, r.ruht_rechtsgrundlage, v.jahr,
       v.steuerart::text AS steuerart
FROM profil.rechtsbehelf r LEFT JOIN profil.veranlagung v ON v.id = r.veranlagung_id
WHERE r.gesellschaft_id = %s ORDER BY r.id
"""

_ASSESSMENTS = """
SELECT jahr, steuerart::text AS steuerart, stand, vorbehalt_164, vorlaeufig_165,
       ablaufhemmung, verjaehrt
FROM profil.veranlagung WHERE gesellschaft_id = %s ORDER BY jahr, steuerart
"""

# Every name column of the profile: none of these may appear in an embedded sentence.
_NAMES = """
SELECT g.name FROM profil.gesellschaft g WHERE g.deal_id = %(deal)s
UNION SELECT kaeufer FROM profil.deal WHERE id = %(deal)s
UNION SELECT verkaeufer FROM profil.deal WHERE id = %(deal)s
UNION SELECT dd_ersteller FROM profil.deal WHERE id = %(deal)s
UNION SELECT dateiname FROM profil.dokument WHERE deal_id = %(deal)s
UNION SELECT gesellschafter FROM profil.beteiligung WHERE gesellschaft_id = %(ges)s
UNION SELECT abgebender FROM profil.anteilswechsel WHERE gesellschaft_id = %(ges)s
UNION SELECT erwerber FROM profil.anteilswechsel WHERE gesellschaft_id = %(ges)s
UNION SELECT organtraeger_name FROM profil.organschaft WHERE organgesellschaft_id = %(ges)s
UNION SELECT bezeichnung FROM profil.auslandsbezug WHERE gesellschaft_id = %(ges)s
UNION SELECT darlehensgeber FROM profil.darlehen WHERE darlehensnehmer_id = %(ges)s
UNION SELECT abgetreten_an FROM profil.darlehen WHERE darlehensnehmer_id = %(ges)s
UNION SELECT vertragspartner FROM profil.nutzungsvertrag WHERE gesellschaft_id = %(ges)s
UNION SELECT einbringende FROM profil.umwandlung WHERE gesellschaft_id = %(ges)s
"""


async def target_company(conn: AsyncConnection, company_id: str) -> int | None:
    """Id of the project's target company (gesellschaft.ist_zielgesellschaft)."""
    cur = await conn.execute(
        "SELECT g.id FROM profil.gesellschaft g JOIN profil.deal d ON d.id = g.deal_id "
        "WHERE d.projektname = %s ORDER BY g.ist_zielgesellschaft DESC, g.id LIMIT 1",
        (company_id,),
    )
    row = await cur.fetchone()
    return row[0] if row else None


async def load_snapshot(conn: AsyncConnection, company_id: str) -> ProfileSnapshot:
    async with conn.cursor(row_factory=dict_row) as cur:
        await cur.execute(
            "SELECT d.id AS deal, g.id AS ges FROM profil.deal d "
            "JOIN profil.gesellschaft g ON g.deal_id = d.id WHERE d.projektname = %s "
            "ORDER BY g.ist_zielgesellschaft DESC, g.id LIMIT 1",
            (company_id,),
        )
        ids = await cur.fetchone()
        if ids is None:
            raise RepositoryError(f"project '{company_id}' has no profile in schema profil")

        async def rows(sql: str, params) -> list[dict]:
            await cur.execute(sql, params)
            return await cur.fetchall()

        names = [r["name"] for r in await rows(_NAMES, ids) if r["name"]]
        return ProfileSnapshot(
            company_id=company_id,
            gesellschaft_id=ids["ges"],
            names=sorted(set(names)),
            merkmale=[MerkmalRow.model_validate(r) for r in await rows(_MERKMALE, (ids["ges"],))],
            findings=[FindingRow.model_validate(r) for r in await rows(_FINDINGS, (ids["deal"],))],
            appeals=[AppealRow.model_validate(r) for r in await rows(_APPEALS, (ids["ges"],))],
            assessments=[
                AssessmentRow.model_validate(r) for r in await rows(_ASSESSMENTS, (ids["ges"],))
            ],
        )
