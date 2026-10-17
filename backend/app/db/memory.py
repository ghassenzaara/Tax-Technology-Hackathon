"""In-memory repository: the default until a database is chosen, and used by most tests.

Stores deep copies, so callers cannot change stored rows by accident (like a real DB).
The profile tables exist in Postgres only: publishing records the draft, confirming records
the confirmed draft, and step 9 reads its features from that confirmed draft (row ids are
positions; Merkmal names and norm titles are unknown without the vocabulary tables).
"""

from datetime import UTC, datetime

from app.config import Settings
from app.db.repository import (
    WORKING_TABLES,
    BusyError,
    DuplicateLawError,
    PublishReport,
    Repository,
    RepositoryError,
    StaleDraftError,
    StateError,
    refused_publish,
)
from app.models.audit import AuditEntry, AuditFilter, AuditKind
from app.models.extraction import Candidate, DocumentExtraction
from app.models.features import (
    AppealRow,
    AssessmentRow,
    EmbeddingRecord,
    FindingRow,
    MerkmalRow,
    NormRef,
    ProfileSnapshot,
)
from app.models.law import Law, LawSection
from app.models.match import CompanyFeatures, Match
from app.models.profile import FieldStatus, ProfileDraft
from app.models.project import EMBEDDABLE, Confirmation, ProjectState, ProjectStatus
from app.models.review import HintRow
from app.models.run import Run, RunDocument, RunState
from app.profile.review import draft_version
from app.profile.schema import ProfileSchema

GESELLSCHAFT_ID = 1  # the one target company of a memory project
# Item columns that hold names of parties (kept out of embedded sentences).
NAME_COLUMNS = frozenset(
    {
        "gesellschafter",
        "abgebender",
        "erwerber",
        "organtraeger_name",
        "bezeichnung",
        "darlehensgeber",
        "abgetreten_an",
        "vertragspartner",
        "einbringende",
    }
)


class MemoryRepository(Repository):
    def __init__(self) -> None:
        self.candidates: dict[tuple[str, str], list[Candidate]] = {}  # (company, doc) -> rows
        self.extractions: dict[tuple[str, str], DocumentExtraction] = {}
        self.drafts: dict[str, list[ProfileDraft]] = {}
        self.published: dict[str, ProfileDraft] = {}
        self.states: dict[str, ProjectStatus] = {}
        self.embeddings: dict[str, list[EmbeddingRecord]] = {}
        self.audit: list[AuditEntry] = []  # append-only, like the database table
        self.runs: dict[str, Run] = {}
        self.run_documents: dict[tuple[str, str], RunDocument] = {}  # (run, path) -> row
        self.norm_codes: set[str] = set()  # stand-in for profil.norm
        self.laws: dict[int, Law] = {}
        self.sections: dict[int, LawSection] = {}
        self.matches: dict[tuple[str, int], Match] = {}  # (company, law section) -> row
        self._next_id = 0

    async def initialize(self) -> None:
        pass

    async def replace_document_candidates(self, extraction: DocumentExtraction) -> None:
        key = (extraction.company_id, extraction.doc_id)
        record = extraction.model_copy(update={"candidates": []}, deep=True)
        previous = self.extractions.get(key)
        if extraction.status.has_result:
            self.candidates[key] = [c.model_copy(deep=True) for c in extraction.candidates]
        elif previous is not None:  # keep the earlier candidates and document metadata
            for name in ("document_class", "document_title", "document_date"):
                if getattr(record, name) is None:
                    setattr(record, name, getattr(previous, name))
        self.extractions[key] = record

    async def list_candidates(self, company_id: str) -> list[Candidate]:
        return [
            c.model_copy(deep=True)
            for (company, _), rows in sorted(self.candidates.items())
            if company == company_id
            for c in rows
        ]

    async def list_extractions(self, company_id: str) -> list[DocumentExtraction]:
        return [
            e.model_copy(deep=True)
            for (company, _), e in sorted(self.extractions.items())
            if company == company_id
        ]

    async def save_profile_draft(self, draft: ProfileDraft) -> None:
        self.drafts.setdefault(draft.company_id, []).append(draft.model_copy(deep=True))

    async def get_profile_draft(self, company_id: str) -> ProfileDraft | None:
        drafts = self.drafts.get(company_id)
        return drafts[-1].model_copy(deep=True) if drafts else None

    async def publish_profile(
        self,
        draft: ProfileDraft,
        extractions: list[DocumentExtraction],
        schema: ProfileSchema,
        settings: Settings,
    ) -> PublishReport:
        cid = draft.company_id
        state = self.states.get(cid)
        if state is not None and state.state is not ProjectState.PUBLISHED:
            raise refused_publish(cid, state.state)
        self.published[cid] = draft.model_copy(deep=True)
        self.states[cid] = ProjectStatus(
            company_id=cid,
            state=ProjectState.PUBLISHED,
            published_at=datetime.now(UTC),
            published_docs=sorted(e.doc_id for e in extractions),
        )
        return PublishReport(project=cid, hints=len(_conflicts(draft)))

    # Steps 8 to 10 -------------------------------------------------------------------

    async def get_project_state(self, company_id: str) -> ProjectStatus | None:
        state = self.states.get(company_id)
        return state.model_copy(deep=True) if state else None

    async def record_error(self, company_id: str, error: str) -> None:
        if company_id in self.states:
            self.states[company_id].last_error = error

    async def list_open_hints(self, company_id: str) -> list[HintRow]:
        """Open conflicts of the published draft, like the writer's konflikt hints."""
        draft = self.published.get(company_id)
        state = self.states.get(company_id)
        if draft is None or state is None or state.state is not ProjectState.PUBLISHED:
            return []
        return [
            HintRow(id=n, art="konflikt", feld=label, beschreibung="Abweichende Werte")
            for n, label in enumerate(_conflicts(draft), start=1)
        ]

    async def confirm_profile(
        self,
        draft: ProfileDraft,
        extractions: list[DocumentExtraction],
        schema: ProfileSchema,
        settings: Settings,
        confirmation: Confirmation,
    ) -> PublishReport:
        cid = draft.company_id
        state = self.states.get(cid)
        if state is None or state.state is not ProjectState.PUBLISHED:
            current = state.state if state else "not published"
            raise StateError(f"project '{cid}' is {current}: confirm needs state published")
        latest = await self.get_profile_draft(cid)
        current = draft_version(latest) if latest else None
        if current != confirmation.draft_version:
            raise StaleDraftError(
                f"draft version {confirmation.draft_version} is stale (current: {current}); "
                "reload the review"
            )
        conflicts = _conflicts(draft)
        closed = sum(1 for _, decided in conflicts.items() if decided)
        self.published[cid] = draft.model_copy(deep=True)
        state.state = ProjectState.CONFIRMED
        state.confirmed_by = confirmation.by
        state.confirmed_at = datetime.now(UTC)
        state.draft_version = confirmation.draft_version
        state.profile_version = 1
        state.last_error = None
        self.audit.append(
            AuditEntry(
                id=len(self.audit) + 1,
                kind=AuditKind.PROFILE_CONFIRMED,
                company_id=cid,
                acted_by=confirmation.by,
                acted_at=datetime.now(UTC),
                new_value={
                    "draft_version": confirmation.draft_version,
                    "counts": confirmation.counts,
                },
                profile_version=1,
            )
        )
        return PublishReport(project=cid, hints=len(conflicts), hints_closed=closed)

    async def load_profile_snapshot(
        self, company_id: str, schema: ProfileSchema
    ) -> ProfileSnapshot:
        state = self.states.get(company_id)
        draft = self.published.get(company_id)
        if draft is None or state is None:
            raise RepositoryError(f"project '{company_id}' has no published profile")
        return snapshot_from_draft(draft, schema)

    async def replace_embeddings(
        self,
        company_id: str,
        model: str,
        records: list[EmbeddingRecord],
        fallback: int,
    ) -> None:
        state = self.states.get(company_id)
        if state is None or state.state not in EMBEDDABLE:
            current = state.state if state else "unknown"
            raise StateError(
                f"project '{company_id}' is {current}: embedding needs a confirmed profile"
            )
        refs = [r.ref for r in records]
        if len(set(refs)) != len(refs):  # the unique key (objekt_typ, objekt_id, modell)
            raise RepositoryError("duplicate embedding for one object")
        kept = [r for r in self.embeddings.get(company_id, []) if r.modell != model]
        self.embeddings[company_id] = kept + [r.model_copy(deep=True) for r in records]
        if state.state is not ProjectState.CLEANED:  # states never move back
            state.state = ProjectState.EMBEDDED
        state.embedded_at = datetime.now(UTC)
        state.embed_model = model
        state.embeddings_expected = state.embeddings_written = len(records)
        state.embeddings_fallback = fallback
        state.expected_features = sorted(refs)
        state.embeddings_stale = False
        state.last_error = None

    async def list_embedding_keys(self, company_id: str, model: str) -> set[str]:
        return {r.ref for r in self.embeddings.get(company_id, []) if r.modell == model}

    async def list_embeddings(self, company_id: str, model: str) -> list[EmbeddingRecord]:
        return [
            r.model_copy(deep=True)
            for r in self.embeddings.get(company_id, [])
            if r.modell == model
        ]

    async def list_audit(self, audit_filter: AuditFilter) -> list[AuditEntry]:
        f = audit_filter
        entries = [
            e
            for e in self.audit
            if (f.company_id is None or e.company_id == f.company_id)
            and (f.kind is None or e.kind is f.kind)
            and (f.acted_by is None or e.acted_by == f.acted_by)
            and (f.since is None or e.acted_at >= f.since)
            and (f.until is None or e.acted_at <= f.until)
        ]
        newest = sorted(entries, key=lambda e: (e.acted_at, e.id), reverse=True)
        return [e.model_copy(deep=True) for e in newest[: f.limit]]

    async def begin_step(self, company_id: str, step: str, timeout_s: float) -> None:
        state = self.states.get(company_id)
        if state is None:
            return
        now = datetime.now(UTC)
        running = state.running_step and state.running_since
        if running and (now - state.running_since).total_seconds() < timeout_s:
            raise BusyError(f"project '{company_id}' is busy ({state.running_step} running); retry")
        state.running_step, state.running_since = step, now

    async def end_step(self, company_id: str) -> None:
        if company_id in self.states:
            self.states[company_id].running_step = None
            self.states[company_id].running_since = None

    async def delete_working_data(self, company_id: str) -> dict[str, int]:
        counts = await self.count_working_rows(company_id)
        for key in [k for k in self.candidates if k[0] == company_id]:
            del self.candidates[key]
        for key in [k for k in self.extractions if k[0] == company_id]:
            del self.extractions[key]
        self.drafts.pop(company_id, None)
        for key in [k for k, d in self.run_documents.items() if d.company_id == company_id]:
            del self.run_documents[key]
        for run in self.runs.values():
            if run.company_id == company_id:
                run.folder = None
        return counts

    async def count_working_rows(self, company_id: str) -> dict[str, int]:
        rows = (
            sum(len(v) for k, v in self.candidates.items() if k[0] == company_id),
            sum(1 for k in self.extractions if k[0] == company_id),
            len(self.drafts.get(company_id, [])),
            sum(1 for d in self.run_documents.values() if d.company_id == company_id),
        )
        return dict(zip(WORKING_TABLES, rows, strict=True))

    # Step 0: runs of the upload layer -----------------------------------------------------

    async def ping(self) -> None:
        pass

    async def save_run(self, run: Run) -> None:
        self.runs[run.run_id] = run.model_copy(deep=True)

    async def get_run(self, run_id: str) -> Run | None:
        run = self.runs.get(run_id)
        return run.model_copy(deep=True) if run else None

    async def list_runs(self, company_id: str | None = None) -> list[Run]:
        runs = [r for r in self.runs.values() if company_id is None or r.company_id == company_id]
        newest = sorted(runs, key=lambda r: (r.created_at, r.run_id), reverse=True)
        return [r.model_copy(deep=True) for r in newest]

    async def save_run_documents(self, documents: list[RunDocument]) -> None:
        for d in documents:
            self.run_documents[(d.run_id, d.relative_path)] = d.model_copy(deep=True)

    async def list_run_documents(self, run_id: str) -> list[RunDocument]:
        return [
            d.model_copy(deep=True)
            for (run, _), d in sorted(self.run_documents.items())
            if run == run_id
        ]

    async def interrupt_active_runs(self, error: str) -> int:
        active = [r for r in self.runs.values() if r.state.active]
        for run in active:
            run.state, run.finished_at = RunState.INTERRUPTED, datetime.now(UTC)
            run.last_error = error
        return len(active)

    async def list_project_states(self) -> list[ProjectStatus]:
        return [s.model_copy(deep=True) for _, s in sorted(self.states.items())]

    async def company_exists(self, company_id: str) -> bool:
        return (
            company_id in self.states
            or company_id in self.published
            or company_id in self.drafts
            or any(r.company_id == company_id for r in self.runs.values())
            or any(k[0] == company_id for k in self.extractions)
        )

    async def mark_cleaned(self, company_id: str) -> None:
        state = self.states.get(company_id)
        if state is None or state.state is not ProjectState.EMBEDDED:
            raise StateError(f"project '{company_id}' is not embedded: cannot mark cleaned")
        state.state = ProjectState.CLEANED
        state.cleaned_at = datetime.now(UTC)
        state.last_error = None

    # Steps 11 to 15: laws and screening -------------------------------------------------

    def _new_id(self) -> int:
        self._next_id += 1
        return self._next_id

    async def list_norm_codes(self) -> set[str]:
        return set(self.norm_codes)

    async def find_law(self, content_hash: str) -> Law | None:
        law = next((x for x in self.laws.values() if x.content_hash == content_hash), None)
        return law.model_copy(deep=True) if law else None

    async def save_law(self, law: Law, sections: list[LawSection], *, replace: bool) -> int:
        old = await self.find_law(law.content_hash)
        if old is not None and not replace:
            raise DuplicateLawError(f"law '{old.title}' (id {old.id}) is already stored")
        if old is not None:  # cascade, like the foreign keys
            del self.laws[old.id]
            self.sections = {k: v for k, v in self.sections.items() if v.law_id != old.id}
            self.matches = {k: v for k, v in self.matches.items() if v.law_id != old.id}
        law_id = self._new_id()
        self.laws[law_id] = law.model_copy(
            update={"id": law_id, "ingested_at": datetime.now(UTC)}, deep=True
        )
        for section in sections:
            section_id = self._new_id()
            self.sections[section_id] = section.model_copy(
                update={"id": section_id, "law_id": law_id}, deep=True
            )
        return law_id

    async def list_laws(self) -> list[Law]:
        return [
            law.model_copy(
                update={"sections": sum(s.law_id == law_id for s in self.sections.values())},
                deep=True,
            )
            for law_id, law in sorted(self.laws.items())
        ]

    async def list_law_sections(self, law_id: int | None = None) -> list[LawSection]:
        rows = [s for s in self.sections.values() if law_id is None or s.law_id == law_id]
        return [s.model_copy(deep=True) for s in sorted(rows, key=lambda s: (s.law_id, s.nr))]

    async def update_law_change_summary(self, law_id: int, change_summary: str | None) -> None:
        if law_id not in self.laws:
            raise RepositoryError(f"no law with id {law_id}")
        self.laws[law_id].change_summary = change_summary

    async def company_features(self, company_id: str) -> CompanyFeatures:
        draft = self.published.get(company_id)
        rechtsform = None
        if draft is not None and "rechtsform" in draft.schema_fields:
            rechtsform = draft.field("rechtsform").chosen
        return CompanyFeatures(
            company_id=company_id,
            gesellschaft_id=GESELLSCHAFT_ID,
            rechtsform=rechtsform if isinstance(rechtsform, str) else None,
            features=[r.model_copy(deep=True) for r in self.embeddings.get(company_id, [])],
        )

    async def list_matches(self, company_id: str, only_hits: bool = False) -> list[Match]:
        rows = [
            m.model_copy(
                update={
                    "section_ref": self.sections[m.law_section_id].section_ref,
                    "law_title": self.laws[m.law_id].title,
                },
                deep=True,
            )
            for (company, _), m in self.matches.items()
            if company == company_id and (m.judge_relevant or not only_hits)
        ]
        return sorted(rows, key=lambda m: (not m.judge_relevant, -m.score, m.law_section_id))

    async def save_matches(self, matches: list[Match]) -> None:
        for m in matches:
            key = (m.company_id, m.law_section_id)
            old = self.matches.get(key)
            self.matches[key] = m.model_copy(
                update={
                    "id": old.id if old else self._new_id(),
                    "score": round(m.score, 4),  # numeric(5,4)
                    "created_at": datetime.now(UTC),
                },
                deep=True,
            )

    async def pending_profile_changes(self, company_id: str) -> list[int]:
        return [
            e.id
            for e in self.audit
            if e.company_id == company_id
            and e.kind is AuditKind.PROFILE_CHANGED
            and e.screened_at is None
        ]

    async def mark_screened(self, entry_ids: list[int]) -> None:
        for e in self.audit:
            if e.id in entry_ids and e.screened_at is None:
                e.screened_at = datetime.now(UTC)


def _conflicts(draft: ProfileDraft) -> dict[str, bool]:
    """Conflict labels of a draft -> decided by the advisor (mirrors the writer's hints)."""
    conflicts = {}
    for f in draft.fields:
        if f.status is FieldStatus.CONFLICT and not f.items:
            conflicts[f.name] = f.decision is not None
        for item in f.items:
            if item.status is FieldStatus.CONFLICT:
                conflicts[f"{f.name} {item.key}"] = item.decision is not None
    return conflicts


def _rows(draft: ProfileDraft, schema: ProfileSchema, table: str) -> list[dict]:
    """Written rows of one profil table (dropped rows excluded)."""
    spec = next((f for f in schema.fields if f.is_list and f.table == table), None)
    if spec is None or spec.name not in draft.schema_fields:
        return []
    return [dict(i.chosen) for i in draft.field(spec.name).items if not i.dropped and i.chosen]


def _norms(codes: list[str] | None) -> list[NormRef]:
    """Norm codes end with their statute ('§ 8c KStG' -> KStG)."""
    return [NormRef(code=c, gesetz=c.split()[-1]) for c in codes or []]


def snapshot_from_draft(draft: ProfileDraft, schema: ProfileSchema) -> ProfileSnapshot:
    """The memory stand-in for reading `profil`: rows of the (confirmed) draft."""
    names = [
        v
        for name in ("name", "kaeufer", "verkaeufer", "dd_ersteller")
        if name in draft.schema_fields and isinstance(v := draft.field(name).chosen, str)
    ]
    for spec in schema.fields:
        if spec.is_list:
            for row in _rows(draft, schema, spec.table or spec.name):
                names += [v for k, v in row.items() if k in NAME_COLUMNS and isinstance(v, str)]
    return ProfileSnapshot(
        company_id=draft.company_id,
        gesellschaft_id=GESELLSCHAFT_ID,
        names=sorted(set(names)),
        merkmale=[
            MerkmalRow(id=n, code=r["merkmal_code"], wert=r["wert"], kurztext=r.get("kurztext"))
            for n, r in enumerate(_rows(draft, schema, "merkmal_wert"), start=1)
        ],
        findings=[
            FindingRow(
                id=n,
                titel=r["titel"],
                kurzsachverhalt=r.get("kurzsachverhalt"),
                richtung=r["richtung"],
                steuerarten=r.get("steuerarten") or [],
                jahr_von=r.get("jahr_von"),
                jahr_bis=r.get("jahr_bis"),
                normen=_norms(r.get("normen")),
            )
            for n, r in enumerate(_rows(draft, schema, "finding"), start=1)
        ],
        appeals=[
            AppealRow(
                id=n,
                art=r["art"],
                status=r["status"],
                streitpunkt=r.get("streitpunkt"),
                ruht_rechtsgrundlage=r.get("ruht_rechtsgrundlage"),
                jahr=r.get("veranlagung_jahr"),
                steuerart=r.get("veranlagung_steuerart"),
            )
            for n, r in enumerate(_rows(draft, schema, "rechtsbehelf"), start=1)
        ],
        assessments=[
            AssessmentRow(
                jahr=r["jahr"],
                steuerart=r["steuerart"],
                stand=r["stand"],
                vorbehalt_164=r["vorbehalt_164"],
                vorlaeufig_165=bool(r.get("vorlaeufig_165")),
                ablaufhemmung=r.get("ablaufhemmung"),
                verjaehrt=bool(r.get("verjaehrt")),
            )
            for r in _rows(draft, schema, "veranlagung")
        ],
    )
