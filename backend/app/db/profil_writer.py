"""Publish a profile draft into the teammate's schema `profil` (backend/db_scheme).

One transaction per project. Scalar fields fill `deal` and `gesellschaft` (the target
company); list fields become rows of their table, each with its own provenance (quelle_dok,
fundstelle, stand, konfidenz) from the strongest supporting candidate. Rows are unconfirmed:
`bestaetigt_von` stays empty until the advisor confirms (step 8).

Conflicts are written with the suggested value (strongest source class, then most
documents) and recorded in `pruefhinweis` (art konflikt, status offen) with all
alternatives, as the schema's design asks. Rows missing a NOT NULL column are skipped and
recorded as pruefhinweis plausibilitaet. Merkmale without any statement become `unbekannt`.
Column types come from the live catalog, so every value is cast to its column type.
"""

import re
from typing import Any

import psycopg
from psycopg import AsyncConnection
from psycopg.rows import dict_row

from app.config import Settings
from app.db.repository import PublishError, PublishReport
from app.models.extraction import Candidate, DocumentExtraction
from app.models.profile import CandidateGroup, FieldResult, FieldStatus, ProfileDraft
from app.profile.merge import WEAKEST_RANK, document_ranks
from app.profile.schema import FieldSpec, ProfileSchema

SCHEMA = "profil"
_DOC_NO = re.compile(r"^(\d+)")


class ProfilWriter:
    def __init__(
        self,
        conn: AsyncConnection,
        draft: ProfileDraft,
        extractions: list[DocumentExtraction],
        schema: ProfileSchema,
        settings: Settings,
    ):
        self.conn = conn
        self.draft = draft
        self.extractions = extractions
        self.schema = schema
        self.settings = settings
        self.ranks = document_ranks(extractions, schema)
        self.report = PublishReport(project=draft.company_id)
        self.columns: dict[str, dict[str, str]] = {}  # table -> column -> SQL type
        self.dokument_ids: dict[str, int] = {}  # doc_id -> profil.dokument.id
        self.document_dates: dict[str, str | None] = {}
        self.deal_id = self.gesellschaft_id = 0
        self.gesellschaft_name = ""
        self.links: dict[str, dict[tuple, int]] = {}  # table -> link key -> row id
        self.conflicts: list[tuple[str, FieldResult]] = []
        self.audits: list[dict[str, Any]] = []  # inserted aussenpruefung rows

    async def publish(self) -> PublishReport:
        await self._load_columns()
        await self._delete_previous()
        await self._insert_deal()
        await self._insert_dokumente()
        await self._insert_gesellschaft()
        for spec in self.schema.fields:
            if spec.is_list:
                await self._insert_list(spec)
        await self._fill_unknown_merkmale()
        await self.write_conflict_hints()
        return self.report

    # Catalog and cleanup -------------------------------------------------------------

    async def _load_columns(self) -> None:
        async with self.conn.cursor() as cur:
            await cur.execute(
                # Cast to the base type without length/precision: an explicit cast to
                # char(2) would silently truncate, an assignment to it raises instead.
                "SELECT c.relname, a.attname, CASE WHEN t.typcategory = 'S' THEN 'text' "
                "ELSE format_type(a.atttypid, NULL) END "
                "FROM pg_attribute a JOIN pg_class c ON c.oid = a.attrelid "
                "JOIN pg_namespace n ON n.oid = c.relnamespace "
                "JOIN pg_type t ON t.oid = a.atttypid "
                "WHERE n.nspname = %s AND c.relkind = 'r' AND a.attnum > 0 "
                "AND NOT a.attisdropped",
                (SCHEMA,),
            )
            for table, column, type_ in await cur.fetchall():
                self.columns.setdefault(table, {})[column] = type_
        if "deal" not in self.columns:
            raise PublishError(f"schema '{SCHEMA}' not found: run the CLI with --init-db")

    async def _delete_previous(self) -> None:
        """Replace an earlier publish of the same project. Rows are deleted from the leaves
        up, because several foreign keys (quelle_dok, finding_id, the grundstueck and
        organschaft links) have no ON DELETE CASCADE."""
        async with self.conn.cursor() as cur:
            await cur.execute(
                f"SELECT id FROM {SCHEMA}.deal WHERE projektname = %s", (self.draft.company_id,)
            )
            row = await cur.fetchone()
            await cur.execute(
                "INSERT INTO pipeline.publications (company_id) VALUES (%s) "
                "ON CONFLICT (company_id) DO UPDATE SET published_at = now() "
                "RETURNING (xmax = 0) AS first_publish",
                (self.draft.company_id,),
            )
            (first_publish,) = await cur.fetchone()
            if row is None:
                return
            if first_publish:  # the project exists but was not published by the pipeline
                raise PublishError(
                    f"project '{self.draft.company_id}' already exists in {SCHEMA} and was not "
                    "published by the pipeline (for example a seed); use another --company-id"
                )
            deal = row[0]
            await cur.execute(f"SELECT id FROM {SCHEMA}.gesellschaft WHERE deal_id = %s", (deal,))
            ids = [r[0] for r in await cur.fetchall()]
            for sql, params in (
                ("DELETE FROM {s}.alert WHERE gesellschaft_id = ANY(%s)", (ids,)),
                (
                    "DELETE FROM {s}.grundstueck WHERE eigentuemer_id = ANY(%s) "
                    "OR nutzer_id = ANY(%s)",
                    (ids, ids),
                ),
                (
                    "DELETE FROM {s}.organschaft WHERE organgesellschaft_id = ANY(%s) "
                    "OR organtraeger_id = ANY(%s)",
                    (ids, ids),
                ),
                ("DELETE FROM {s}.spa_absicherung WHERE deal_id = %s", (deal,)),
                ("DELETE FROM {s}.finding WHERE deal_id = %s", (deal,)),
                ("DELETE FROM {s}.gesellschaft WHERE deal_id = %s", (deal,)),  # cascades facts
                ("DELETE FROM {s}.deal WHERE id = %s", (deal,)),  # cascades dokument
            ):
                await cur.execute(sql.format(s=SCHEMA), params)

    # Deal, documents, target company -------------------------------------------------

    def _scalars(self, table: str) -> dict[str, FieldResult]:
        return {
            spec.name: self.draft.field(spec.name)
            for spec in self.schema.fields
            if not spec.is_list and spec.table == table and spec.name in self.draft.schema_fields
        }

    def _scalar_values(self, table: str) -> dict[str, Any]:
        values = {}
        for name, result in self._scalars(table).items():
            if result.status is FieldStatus.MISSING:
                continue
            values[name] = result.chosen
            if result.status is FieldStatus.CONFLICT:
                self._conflict_hint(name, result)
        missing = [
            spec.name
            for spec in self.schema.fields
            if spec.table == table and spec.required and values.get(spec.name) is None
        ]
        if missing:
            raise PublishError(f"{table}: no value for required {', '.join(missing)}")
        return values

    async def _insert_deal(self) -> None:
        values = self._scalar_values("deal") | {"projektname": self.draft.company_id}
        self.deal_id = await self._insert("deal", values)

    async def _insert_dokumente(self) -> None:
        used: set[str] = set()
        for n, e in enumerate(sorted(self.extractions, key=lambda e: e.filename), start=1):
            if e.document_class not in self.schema.document_classes:
                # dokument.klasse_code is NOT NULL: no row. Facts from this document keep
                # their file name in fundstelle but get no quelle_dok.
                if e.status.has_result:
                    self.report.skipped.append(f"dokument {e.filename}: no document class")
                continue
            match = _DOC_NO.match(e.filename.rsplit("/", 1)[-1])
            dok_nr = match.group(1) if match and match.group(1) not in used else f"x{n:02d}"
            used.add(dok_nr)
            self.document_dates[e.doc_id] = e.document_date
            self.dokument_ids[e.doc_id] = await self._insert(
                "dokument",
                {
                    "deal_id": self.deal_id,
                    "dok_nr": dok_nr,
                    "titel": e.document_title or e.filename,
                    "dateiname": e.filename,
                    "klasse_code": e.document_class,
                    "dokument_datum": e.document_date,
                },
            )

    async def _insert_gesellschaft(self) -> None:
        values = self._scalar_values("gesellschaft")
        provenance = self._provenance("gesellschaft", self.draft.field("name").chosen_group)
        self.gesellschaft_name = values["name"]
        self.gesellschaft_id = await self._insert(
            "gesellschaft",
            values | provenance | {"deal_id": self.deal_id, "ist_zielgesellschaft": True},
        )

    # List fields ---------------------------------------------------------------------

    async def _insert_list(self, spec: FieldSpec) -> None:
        if spec.name not in self.draft.schema_fields:
            return
        table = spec.table or spec.name
        for item in self.draft.field(spec.name).items:
            value = dict(item.chosen)
            label = f"{spec.name} {_label(item.key)}"
            missing = [i.name for i in spec.items if i.required and value.get(i.name) is None]
            if missing:
                await self._hint("plausibilitaet", label, f"Zeile übersprungen, fehlt: {missing}")
                self.report.skipped.append(f"{label}: missing {', '.join(missing)}")
                continue
            row = {i.name: value[i.name] for i in spec.items if i.db and value[i.name] is not None}
            parent = await self._parent(spec, table, value, row, label)
            if parent is None:
                continue
            row |= parent | self._provenance(table, item.chosen_group)
            try:
                async with self.conn.transaction():  # savepoint: a bad row skips only itself
                    row_id = await self._insert(table, row)
                    await self._after_insert(table, value, row_id)
            except (PublishError, psycopg.Error) as exc:
                await self._hint("plausibilitaet", label, f"Zeile übersprungen: {exc}")
                self.report.skipped.append(f"{label}: {exc}")
                continue
            if item.status is FieldStatus.CONFLICT:
                self._conflict_hint(label, item)

    async def _parent(
        self, spec: FieldSpec, table: str, value: dict, row: dict, label: str
    ) -> dict[str, Any] | None:
        """Foreign keys of a list row; None when the row cannot be linked."""
        column = spec.parent_column or "gesellschaft_id"
        if column == "deal_id":
            parent = {"deal_id": self.deal_id}
            if table == "finding":
                parent["gesellschaft_id"] = self.gesellschaft_id
            if table == "spa_absicherung" and value.get("finding_code"):
                finding = self.links.get("finding", {}).get((value["finding_code"],))
                if finding:
                    parent["finding_id"] = finding
            return parent
        if column == "aussenpruefung_id":  # pruefungsfeststellung
            audit = self._audit_for(value)
            if audit is None:
                await self._hint("plausibilitaet", label, "Zeile übersprungen: keine Prüfung")
                self.report.skipped.append(f"{label}: no aussenpruefung to attach it to")
                return None
            return {"aussenpruefung_id": audit}
        parent = {column: self.gesellschaft_id}
        if table == "organschaft":
            parent["organgesellschaft_name"] = self.gesellschaft_name
        if table == "grundstueck" and value.get("eigentum_status") == "eigentum":
            parent["eigentuemer_id"] = self.gesellschaft_id
        if table == "rechtsbehelf" and value.get("veranlagung_jahr"):
            veranlagung = next(
                (
                    row_id
                    for (jahr, steuerart), row_id in self.links.get("veranlagung", {}).items()
                    if jahr == value["veranlagung_jahr"]
                    and steuerart == (value.get("veranlagung_steuerart") or steuerart)
                ),
                None,
            )
            if veranlagung:
                parent["veranlagung_id"] = veranlagung
        return parent

    def _audit_for(self, finding: dict) -> int | None:
        """Audit a finding (C08) belongs to: same period, else one matching year, else the
        latest finished audit (C08 lists the findings of the last finished audit)."""
        audits = self.audits
        if not audits:
            return None
        von, bis = finding.get("pruefung_zeitraum_von"), finding.get("pruefung_zeitraum_bis")
        for match in (
            lambda a: (a["von"], a["bis"]) == (von, bis),
            lambda a: (
                (von is not None and a["von"] == von) or (bis is not None and a["bis"] == bis)
            ),
        ):
            hits = [a for a in audits if match(a)]
            if len(hits) == 1:
                return hits[0]["id"]
        finished = [a for a in audits if a["status"] == "abgeschlossen"] or audits
        return max(finished, key=lambda a: (a["bis"], a["von"]))["id"]

    async def _after_insert(self, table: str, value: dict, row_id: int) -> None:
        if table == "veranlagung":
            self.links.setdefault(table, {})[(value["jahr"], value["steuerart"])] = row_id
        elif table == "aussenpruefung":
            self.audits.append(
                {
                    "von": value["zeitraum_von"],
                    "bis": value["zeitraum_bis"],
                    "status": value["status"],
                    "id": row_id,
                }
            )
        elif table == "finding":
            self.links.setdefault(table, {})[(value["finding_code"],)] = row_id
            for norm in value.get("normen") or []:
                await self.conn.execute(
                    f"INSERT INTO {SCHEMA}.finding_norm (finding_id, norm_code) "
                    f"SELECT %s, code FROM {SCHEMA}.norm WHERE code = %s ON CONFLICT DO NOTHING",
                    (row_id, norm),
                )

    async def _fill_unknown_merkmale(self) -> None:
        """Silence of every source means unbekannt, never nein (schema design rule)."""
        spec = next((f for f in self.schema.fields if f.table == "merkmal_wert"), None)
        if spec is None:
            return
        stated = {
            item.key["merkmal_code"] for item in self.draft.field(spec.name).items if item.key
        }
        for code in spec.item("merkmal_code").enum:
            if code not in stated:
                await self._insert(
                    "merkmal_wert",
                    {
                        "gesellschaft_id": self.gesellschaft_id,
                        "merkmal_code": code,
                        "wert": "unbekannt",
                        "kurztext": "Keine Aussage in den Dokumenten",
                    },
                )

    # Provenance, hints, SQL ----------------------------------------------------------

    def _best(self, group: CandidateGroup | None) -> Candidate | None:
        if group is None:
            return None
        return min(
            group.candidates,
            key=lambda c: (
                c.source_doc not in self.dokument_ids,  # prefer a source with a dokument row
                self.ranks.get(c.source_doc, WEAKEST_RANK + 1),
                -c.confidence,
                c.source_doc,
                c.source_page,
            ),
        )

    def _konfidenz(self, confidence: float) -> str:
        if confidence >= self.settings.konfidenz_hoch_min:
            return "hoch"
        if confidence >= self.settings.konfidenz_mittel_min:
            return "mittel"
        return "niedrig"

    def _provenance(self, table: str, group: CandidateGroup | None) -> dict[str, Any]:
        best = self._best(group)
        columns = self.columns.get(table, {})
        if best is None or "quelle_dok" not in columns:
            return {}
        dokument = self.dokument_ids.get(best.source_doc)
        where = (
            f"S. {best.source_page}"
            if dokument
            else f"{best.source_filename} S. {best.source_page}"
        )
        provenance = {
            "quelle_dok": dokument,
            "fundstelle": f"{where}: {best.evidence[:200]}",
            "konfidenz": self._konfidenz(best.confidence),
        }
        # veranlagung uses `stand` for the assessment status and `stand_meta` for the date.
        date_column = "stand_meta" if "stand_meta" in columns else "stand"
        if date_column in columns and self.document_dates.get(best.source_doc):
            provenance[date_column] = self.document_dates[best.source_doc]
        return provenance

    def _conflict_hint(self, label: str, result: FieldResult) -> None:
        self.conflicts.append((label, result))  # written last, once all documents exist

    async def write_conflict_hints(self) -> None:
        for label, result in self.conflicts:
            chosen = result.groups[result.suggestion]
            others = [g for i, g in enumerate(result.groups) if i != result.suggestion]
            options = "; ".join(f"{_short(g.value)} ({self._sources(g)})" for g in result.groups)
            rank = chosen.best_rank if chosen.best_rank is not None else "?"
            await self._hint(
                "konflikt",
                label,
                f"Abweichende Werte: {options}",
                quelle_a=self._dokument(chosen),
                quelle_b=self._dokument(others[0]) if others else None,
                entscheidung=f"Vorschlag: {_short(chosen.value)} (Rang {rank}), "
                "vom Steuerberater zu bestätigen",
            )

    def _sources(self, group: CandidateGroup) -> str:
        return ", ".join(f"{c.source_filename} S. {c.source_page}" for c in group.candidates)

    def _dokument(self, group: CandidateGroup) -> int | None:
        best = self._best(group)
        return self.dokument_ids.get(best.source_doc) if best else None

    async def _hint(self, art: str, feld: str, beschreibung: str, **extra: Any) -> None:
        await self._insert(
            "pruefhinweis",
            {
                "gesellschaft_id": self.gesellschaft_id or None,
                "art": art,
                "feld": feld[:200],
                "beschreibung": beschreibung,
                "status": "offen",
                **{k: v for k, v in extra.items() if v is not None},
            },
            count=False,
        )
        self.report.hints += 1

    async def _insert(self, table: str, values: dict[str, Any], count: bool = True) -> int:
        columns = self.columns[table]
        unknown = set(values) - set(columns)
        if unknown:
            raise PublishError(f"{table}: no such columns {sorted(unknown)}")
        names = [n for n, v in values.items() if v is not None]
        placeholders = ", ".join(f"%s::{columns[n]}" for n in names)
        sql = (
            f"INSERT INTO {SCHEMA}.{table} ({', '.join(names)}) "
            f"VALUES ({placeholders}) RETURNING id"
        )
        try:
            async with self.conn.cursor(row_factory=dict_row) as cur:
                await cur.execute(sql, [values[n] for n in names])
                row = await cur.fetchone()
        except psycopg.Error as exc:  # bad value for a column type or constraint
            detail = exc.diag.message_primary or str(exc)
            raise PublishError(f"{table}: {detail}") from exc
        if count:
            self.report.rows[table] = self.report.rows.get(table, 0) + 1
        return row["id"]


async def publish_profile(
    conn: AsyncConnection,
    draft: ProfileDraft,
    extractions: list[DocumentExtraction],
    schema: ProfileSchema,
    settings: Settings,
) -> PublishReport:
    """Run inside a transaction: all rows or none."""
    return await ProfilWriter(conn, draft, extractions, schema, settings).publish()


def _label(key: dict[str, Any] | None) -> str:
    return "[" + ", ".join(f"{k}={v}" for k, v in (key or {}).items()) + "]"


def _short(value: Any) -> str:
    text = str(value)
    return text if len(text) <= 160 else text[:157] + "..."
