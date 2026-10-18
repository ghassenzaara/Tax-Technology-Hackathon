"""In-memory repository: the default until a database is chosen, and used by most tests.

Stores deep copies, so callers cannot change stored rows by accident (like a real DB).
The profile tables exist in Postgres only: publishing records the draft, confirming records
the confirmed draft, and step 9 reads its features from that confirmed draft (row ids are
positions; Merkmal names and norm titles are unknown without the vocabulary tables).
"""

from datetime import UTC, datetime, timedelta

from app.config import Settings
from app.db.repository import (
    WORKING_TABLES,
    BusyError,
    DuplicateLawError,
    NotFoundError,
    PublishReport,
    Repository,
    RepositoryError,
    RunActiveError,
    SendPendingError,
    StaleDraftError,
    StateError,
    refused_publish,
)
from app.models.alert import SENDABLE, Alert, AlertStatus
from app.models.audit import AuditEntry, AuditFilter, AuditKind
from app.models.client import ClientInfo, MatchReview, MultiSendRequest, OutreachResponse
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
from app.models.job import Job, JobStatus
from app.models.law import Law, LawSection
from app.models.match import (
    FINAL_INVALIDATIONS,
    CompanyFeatures,
    Match,
    ScreeningChanges,
    ScreeningStatus,
)
from app.models.outreach import EmailTemplate, Outreach, default_template
from app.models.profile import FieldResult, FieldStatus, ProfileDraft
from app.models.project import EMBEDDABLE, Confirmation, ProjectState, ProjectStatus
from app.models.review import HintRow
from app.models.run import Run, RunDocument, RunState
from app.models.update import ADVISOR_SOURCE, ConfirmedProfile, ProfileRow
from app.profile.reconcile import reconciliation_history
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
    def __init__(self, settings: Settings | None = None) -> None:
        self.settings = settings or Settings(_env_file=None)
        self.alerts: dict[int, Alert] = {}
        self.outreach: dict[int, Outreach] = {}
        self.template: EmailTemplate | None = None
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
        self.coverage: dict[tuple[str, int], tuple[bool, int, int]] = {}  # (company, law)
        self.jobs: dict[str, Job] = {}
        self.client_info: dict[str, ClientInfo] = {}
        self.match_reviews: dict[tuple[str, int], MatchReview] = {}  # (company, law) -> row
        self.outreach_responses: dict[int, OutreachResponse] = {}  # outreach id -> reply
        self.seen_laws: dict[int, datetime] = {}
        self._next_id = 0

    async def initialize(self) -> None:
        if self.template is None:
            self.template = default_template(self.settings, datetime.now(UTC))
        for match in list(self.matches.values()):
            self._sync_alert(match)

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
                    "reconciliations": reconciliation_history(draft),
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

    async def remove_profile_sources(self, company_id: str) -> None:
        info = self.client_info.get(company_id)
        if info:
            info.citations = {}
        draft = self.published.get(company_id)
        if draft:
            for field in draft.fields:
                for result in field.items or [field]:
                    for group in result.groups:
                        group.candidates = []
                        group.documents = []
                    result.reconciliation = None
                    if result.decision:
                        result.decision.text = "Wert gespeichert"

    async def delete_working_data(self, company_id: str) -> dict[str, int]:
        counts = await self.count_working_rows(company_id)
        for key in [k for k in self.candidates if k[0] == company_id]:
            del self.candidates[key]
        for key in [k for k in self.extractions if k[0] == company_id]:
            del self.extractions[key]
        self.drafts.pop(company_id, None)
        for key in [k for k, d in self.run_documents.items() if d.company_id == company_id]:
            del self.run_documents[key]
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
        if state is None or state.state not in (ProjectState.EMBEDDED, ProjectState.CLEANED):
            raise StateError(f"project '{company_id}' is not embedded: cannot mark cleaned")
        for run in self.runs.values():
            if run.company_id == company_id:
                run.folder = None
        if state.state is ProjectState.EMBEDDED:
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
            for alert in self.alerts.values():
                if alert.law.id == old.id:
                    alert.match_id = None
                    alert.match.is_current = False
                    if alert.status in SENDABLE:
                        alert.status = AlertStatus.OBSOLETE
                        alert.status_changed_at = datetime.now(UTC)
            del self.laws[old.id]
            self.sections = {k: v for k, v in self.sections.items() if v.law_id != old.id}
            self.matches = {k: v for k, v in self.matches.items() if v.law_id != old.id}
            self.coverage = {k: v for k, v in self.coverage.items() if k[1] != old.id}
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

    async def list_matches(
        self,
        company_id: str,
        only_hits: bool = False,
        *,
        law_id: int | None = None,
        include_history: bool = False,
    ) -> list[Match]:
        rows = [
            m.model_copy(
                update={
                    "section_ref": self.sections[m.law_section_id].section_ref,
                    "law_title": self.laws[m.law_id].title,
                },
                deep=True,
            )
            for (company, _), m in self.matches.items()
            if company == company_id
            and (m.judge_relevant or not only_hits)
            and (law_id is None or m.law_id == law_id)
            and (m.is_current or include_history)
        ]
        return sorted(rows, key=lambda m: (not m.judge_relevant, -m.score, m.law_section_id))

    async def save_matches(self, matches: list[Match]) -> None:
        if any(m.law_id not in self.laws or m.law_section_id not in self.sections for m in matches):
            raise RepositoryError("match references an unknown law or section")
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
            self._sync_alert(self.matches[key])

    async def apply_screening(self, changes: ScreeningChanges) -> None:
        cid, scope = changes.company_id, set(changes.law_ids)
        if any(m.law_id not in scope or m.company_id != cid for m in changes.judged):
            raise RepositoryError("judged match outside the screening scope")
        await self.save_matches(changes.judged)
        for section_id in changes.reused:
            match = self.matches[(cid, section_id)]
            match.is_current, match.invalidation_reason = True, None
            match.profile_version = changes.profile_version
            self._sync_alert(match)
        for section_id, reason in changes.invalidated.items():
            match = self.matches.get((cid, section_id))
            if match is None:
                continue
            match.is_current, match.invalidation_reason = False, reason
            self._sync_alert(match)
        for law_id, (deferred, unjudged) in changes.coverage.items():
            self.coverage[(cid, law_id)] = (not (deferred or unjudged), deferred, unjudged)
        await self.mark_screened(changes.screened_entries)

    async def screening_status(self, company_id: str) -> ScreeningStatus:
        covered = {law for (cid, law) in self.coverage if cid == company_id}
        return ScreeningStatus(
            pending_changes=len(await self.pending_profile_changes(company_id)),
            incomplete_laws=[
                law
                for (cid, law), (complete, _, _) in sorted(self.coverage.items())
                if cid == company_id and not complete
            ],
            unscreened_laws=[law for law in sorted(self.laws) if law not in covered],
            stale_decisions=sum(
                1
                for (cid, _), m in self.matches.items()
                if cid == company_id
                and not m.is_current
                and m.invalidation_reason not in FINAL_INVALIDATIONS
            ),
        )

    def _sync_alert(self, match: Match) -> None:
        """Same transitions as the Postgres alert sync (see postgres._sync_alert)."""
        old = next((a for a in self.alerts.values() if a.match_id == match.id), None)
        now = datetime.now(UTC)
        if old is None and not (match.judge_relevant and match.is_current):
            return
        section = self.sections[match.law_section_id]
        status = old.status if old else AlertStatus.NEW
        if match.is_current:
            if match.judge_relevant and status == AlertStatus.OBSOLETE:
                status = AlertStatus.NEW
            elif not match.judge_relevant and status in SENDABLE:
                status = AlertStatus.OBSOLETE
        elif match.invalidation_reason in FINAL_INVALIDATIONS and status in SENDABLE:
            status = AlertStatus.OBSOLETE
        alert = Alert(
            id=old.id if old else self._new_id(),
            match_id=match.id,
            company_id=match.company_id,
            status=status,
            dismissed_reason=old.dismissed_reason if old else None,
            created_at=old.created_at if old else now,
            status_changed_at=(old.status_changed_at if old and status == old.status else now),
            match=match.model_copy(deep=True),
            law=self.laws[match.law_id].model_copy(deep=True),
            section_ref=section.section_ref,
            section_text=section.text,
            page_from=section.page_from,
            page_to=section.page_to,
        )
        self.alerts[alert.id] = alert

    async def get_contact_email(self, company_id: str) -> str | None:
        draft = self.published.get(company_id)
        if draft and "ansprechpartner_email" in draft.schema_fields:
            return draft.field("ansprechpartner_email").chosen
        return None

    async def list_alerts(self, company_id=None, law_id=None) -> list[Alert]:
        return [
            await self.get_alert(a.id)
            for a in self.alerts.values()
            if (company_id is None or a.company_id == company_id)
            and (law_id is None or a.law.id == law_id)
        ]

    async def get_alert(self, alert_id: int) -> Alert:
        if alert_id not in self.alerts:
            raise NotFoundError(f"no alert with id {alert_id}")
        result = self.alerts[alert_id].model_copy(deep=True)
        live = next((m for m in self.matches.values() if m.id == result.match_id), None)
        if live is not None:  # its freshness decides whether the alert may be sent
            result.match = live.model_copy(deep=True)
        if result.law.id in self.laws:
            result.law = self.laws[result.law.id].model_copy(deep=True)
        result.recipient_available = bool(await self.get_contact_email(result.company_id))
        result.outreach = await self.list_outreach(alert_id=alert_id)
        return result

    async def change_alert_status(self, alert_id, status, reason=None) -> Alert:
        alert = await self.get_alert(alert_id)
        allowed = (
            {AlertStatus.NEW}
            if status == AlertStatus.SEEN
            else SENDABLE
            if status == AlertStatus.DISMISSED
            else set()
        )
        if alert.status not in allowed:
            raise StateError(f"alert {alert_id} is {alert.status}; cannot set {status}")
        if any(o.status == "pending" for o in alert.outreach):
            raise StateError(f"alert {alert_id} has a pending send")
        row = self.alerts[alert_id]
        row.status, row.status_changed_at = status, datetime.now(UTC)
        row.dismissed_reason = reason if status == AlertStatus.DISMISSED else None
        return await self.get_alert(alert_id)

    async def get_template(self) -> EmailTemplate:
        if self.template is None:
            self.template = default_template(self.settings, datetime.now(UTC))
        return self.template.model_copy(deep=True)

    async def edit_template(self, subject: str, body: str, *, reset: bool = False) -> EmailTemplate:
        old = await self.get_template()
        self.template = EmailTemplate(
            subject=subject,
            body=body,
            updated_at=max(datetime.now(UTC), old.updated_at + timedelta(microseconds=1)),
        )
        self.audit.append(
            AuditEntry(
                id=len(self.audit) + 1,
                kind=AuditKind.TEMPLATE_EDITED,
                acted_by=self.settings.advisor_label,
                acted_at=datetime.now(UTC),
                old_value={"subject": old.subject, "body": old.body},
                new_value={"subject": subject, "body": body} | ({"reset": True} if reset else {}),
            )
        )
        return await self.get_template()

    async def reserve_outreach(self, request, delivered_to, delivery_subject) -> Outreach:
        from app.services.alerts import require_sendable

        alert = await self.get_alert(request.alert_id)
        pending = next(
            (
                o
                for o in self.outreach.values()
                if o.company_id == alert.company_id and o.status == "pending"
            ),
            None,
        )
        if pending:
            raise SendPendingError("Vorheriger Versand unklar; bitte zuerst auflösen.", pending.id)
        require_sendable(
            alert,
            alert.outreach,
            await self.get_project_state(alert.company_id),
            await self.company_busy(alert.company_id, self.settings.step_lock_timeout_s),
        )
        template = await self.get_template()
        row = Outreach(
            id=self._new_id(),
            alert_id=alert.id,
            company_id=alert.company_id,
            recipient=request.recipient,
            delivered_to=delivered_to,
            subject=request.subject,
            delivery_subject=delivery_subject,
            sent_body=request.body,
            created_at=datetime.now(UTC),
            template_updated_at=request.template_updated_at or template.updated_at,
        )
        self.outreach[row.id] = row
        return row.model_copy(deep=True)

    async def finish_outreach(
        self, outreach_id, status, *, message_id=None, error=None
    ) -> Outreach:
        row = self.outreach.get(outreach_id)
        if row is None:
            raise NotFoundError(f"no outreach with id {outreach_id}")
        if row.status != "pending" or status not in {"sent", "failed"}:
            raise StateError(f"outreach {outreach_id} is {row.status}; cannot set {status}")
        row.status, row.error = status, error
        law_ids = row.law_ids or ([self.alerts[row.alert_id].law.id] if row.alert_id else [])
        for law_id in law_ids:
            review = self.match_reviews.get((row.company_id, law_id))
            if review is not None and review.pending_outreach_id == row.id:
                review.pending_outreach_id = None
        if status == "sent":
            row.sent_at, row.provider_message_id = datetime.now(UTC), message_id
            for alert in self.alerts.values():
                ours = alert.id == row.alert_id or (
                    alert.company_id == row.company_id
                    and alert.law.id in row.law_ids
                    and alert.status in SENDABLE
                )
                if ours:
                    alert.status, alert.status_changed_at = AlertStatus.ACTIONED, row.sent_at
            for law_id in law_ids:  # the advisor's review follows every send from the app
                review = self.match_reviews.setdefault(
                    (row.company_id, law_id), MatchReview(company_id=row.company_id, law_id=law_id)
                )
                review.email_status, review.sent_at = "sent", row.sent_at
                review.outreach_id, review.updated_at = row.id, row.sent_at
            self.audit.append(
                AuditEntry(
                    id=len(self.audit) + 1,
                    kind=AuditKind.EMAIL_SENT,
                    company_id=row.company_id,
                    acted_by=self.settings.advisor_label,
                    acted_at=row.sent_at,
                    outreach_id=row.id,
                )
            )
        return row.model_copy(deep=True)

    async def list_outreach(self, company_id=None, alert_id=None) -> list[Outreach]:
        return [
            o.model_copy(deep=True)
            for o in sorted(
                self.outreach.values(), key=lambda o: (o.created_at, o.id), reverse=True
            )
            if (company_id is None or o.company_id == company_id)
            and (alert_id is None or o.alert_id == alert_id)
        ]

    # UI contract layer -------------------------------------------------------------------

    async def get_client_info(self, company_id: str) -> ClientInfo | None:
        info = self.client_info.get(company_id)
        return info.model_copy(deep=True) if info else None

    async def save_client_info(self, info: ClientInfo, audit: list[AuditEntry] = ()) -> ClientInfo:
        stored = info.model_copy(update={"updated_at": datetime.now(UTC)}, deep=True)
        self.client_info[info.company_id] = stored
        for entry in audit:
            await self.append_audit(entry)
        return stored.model_copy(deep=True)

    async def save_client_note(self, company_id, notes, actor):
        info = await self.get_client_info(company_id) or ClientInfo(company_id=company_id)
        old, info.notes = info.notes or "", notes
        audit = (
            [
                AuditEntry(
                    company_id=company_id,
                    kind=AuditKind.NOTE_CHANGED,
                    acted_by=actor,
                    field="notes",
                    old_value=old,
                    new_value=notes,
                    screened_at=datetime.now(UTC),
                )
            ]
            if old != notes
            else []
        )
        await self.save_client_info(info, audit)

    async def append_audit(self, entry: AuditEntry) -> None:
        self.audit.append(
            entry.model_copy(
                update={"id": len(self.audit) + 1, "acted_at": datetime.now(UTC)}, deep=True
            )
        )

    async def list_match_reviews(
        self, company_id: str | None = None, law_id: int | None = None
    ) -> list[MatchReview]:
        return [
            r.model_copy(deep=True)
            for (cid, lid), r in sorted(self.match_reviews.items())
            if (company_id is None or cid == company_id) and (law_id is None or lid == law_id)
        ]

    async def save_match_review(
        self, review: MatchReview, audit: list[AuditEntry] = ()
    ) -> MatchReview:
        if review.law_id not in self.laws:
            raise NotFoundError(f"no law with id {review.law_id}")
        old = self.match_reviews.get((review.company_id, review.law_id))
        from app.services.alerts import validate_review_update

        validate_review_update(old, review)
        if old is not None and old.pending_outreach_id is not None:
            raise SendPendingError(
                f"the send of outreach {old.pending_outreach_id} is unclear; resolve it first",
                old.pending_outreach_id,
            )
        stored = review.model_copy(update={"updated_at": datetime.now(UTC)}, deep=True)
        self.match_reviews[(review.company_id, review.law_id)] = stored
        for entry in audit:
            await self.append_audit(entry)
        return stored.model_copy(deep=True)

    async def reset_demo_data(self) -> None:
        if any(j.status in {"queued", "running"} for j in self.jobs.values()) or any(
            r.state in {"queued", "running"} for r in self.runs.values()
        ):
            raise BusyError("work is still running; reset refused")
        template, norm_codes = self.template, self.norm_codes
        self.__init__(self.settings)
        self.template, self.norm_codes = template, norm_codes

    async def list_outreach_responses(self, company_id=None) -> list[OutreachResponse]:
        return [
            r.model_copy(deep=True)
            for _, r in sorted(self.outreach_responses.items())
            if company_id is None or r.company_id == company_id
        ]

    async def save_outreach_response(
        self, response: OutreachResponse, audit: list[AuditEntry] = ()
    ) -> OutreachResponse:
        row = self.outreach.get(response.outreach_id)
        if row is None:
            raise NotFoundError(f"no outreach with id {response.outreach_id}")
        if row.status != "sent" or row.company_id != response.company_id:
            raise StateError(f"outreach {row.id} is {row.status}; no reply can be recorded")
        stored = response.model_copy(update={"recorded_at": datetime.now(UTC)}, deep=True)
        self.outreach_responses[row.id] = stored
        for entry in audit:
            await self.append_audit(entry)
        return stored.model_copy(deep=True)

    async def list_seen_laws(self) -> list[int]:
        return sorted(self.seen_laws)

    async def mark_law_seen(self, law_id: int) -> None:
        if law_id not in self.laws:
            raise NotFoundError(f"no law with id {law_id}")
        self.seen_laws.setdefault(law_id, datetime.now(UTC))

    async def reserve_multi_outreach(
        self, request: MultiSendRequest, delivered_to: str, delivery_subject: str
    ) -> Outreach:
        from app.services.alerts import require_multi_sendable

        cid = request.company_id
        reviews = {law_id: self.match_reviews.get((cid, law_id)) for law_id in request.law_ids}
        pending = next(
            (o for o in self.outreach.values() if o.company_id == cid and o.status == "pending"),
            None,
        )
        current = {
            m.law_id
            for (c, _), m in self.matches.items()
            if c == cid and m.is_current and m.judge_relevant
        }
        require_multi_sendable(
            cid,
            reviews,
            pending.id if pending else None,
            current,
            await self.get_project_state(cid),
            await self.company_busy(cid, self.settings.step_lock_timeout_s),
        )
        template = await self.get_template()
        row = Outreach(
            id=self._new_id(),
            alert_id=None,
            law_ids=list(request.law_ids),
            company_id=cid,
            recipient=request.recipient,
            delivered_to=delivered_to,
            subject=request.subject,
            delivery_subject=delivery_subject,
            sent_body=request.body,
            created_at=datetime.now(UTC),
            template_updated_at=template.updated_at,
        )
        self.outreach[row.id] = row
        for review in reviews.values():
            review.pending_outreach_id = row.id
        return row.model_copy(deep=True)

    async def discard_company(self, company_id: str) -> None:
        state = self.states.get(company_id)
        if state is not None and state.state is not ProjectState.PUBLISHED:
            raise StateError(f"project '{company_id}' is {state.state}: only drafts are discarded")
        if any(r.company_id == company_id and r.state.active for r in self.runs.values()):
            raise RunActiveError(f"a run of '{company_id}' is still active; wait for it")
        await self.delete_working_data(company_id)
        self.states.pop(company_id, None)
        self.published.pop(company_id, None)
        self.client_info.pop(company_id, None)
        self.runs = {k: r for k, r in self.runs.items() if r.company_id != company_id}
        self.jobs = {k: j for k, j in self.jobs.items() if j.company_id != company_id}

    async def read_ui_profile(
        self, company_id: str, schema: ProfileSchema, settings: Settings
    ) -> ConfirmedProfile:
        """The memory stand-in for `read_profile`: rows of the confirmed draft (row ids are
        positions, citations name the file and page of the strongest candidate)."""
        state = self.states.get(company_id)
        draft = self.published.get(company_id)
        if state is None or draft is None or state.state not in EMBEDDABLE:
            raise StateError(f"project '{company_id}' has no confirmed profile")
        scalars, lists = {}, {}
        for spec in schema.fields:
            if spec.name not in draft.schema_fields:
                continue
            result = draft.field(spec.name)
            if spec.is_list:
                lists[spec.name] = [
                    ProfileRow(id=n, values=dict(i.chosen), fundstelle=_citation(i))
                    for n, i in enumerate(result.items, start=1)
                    if not i.dropped and i.chosen
                ]
            elif not result.dropped:
                scalars[spec.name] = result.chosen
        return ConfirmedProfile(
            company_id=company_id,
            state=state.state,
            profile_version=state.profile_version,
            embeddings_stale=state.embeddings_stale,
            scalars=scalars,
            scalar_rows={},
            lists=lists,
            audit=await self.list_audit(AuditFilter(company_id=company_id)),
        )

    async def apply_ui_update(
        self, company_id, version, request, info_patch, audit, schema, settings, today
    ):
        from app.db.repository import StaleProfileError

        state = self.states.get(company_id)
        if state is None or state.state not in EMBEDDABLE:
            raise StateError("profile is not confirmed")
        if state.profile_version != version:
            raise StaleProfileError("profile version is stale")
        busy = await self.company_busy(company_id, settings.step_lock_timeout_s)
        if busy:
            raise BusyError(busy)
        result = await self.update_ui_profile(request, schema, settings, today) if request else None
        if info_patch:
            info = await self.get_client_info(company_id) or ClientInfo(company_id=company_id)
            info = info.model_copy(update=info_patch)
            await self.save_client_info(info, audit)
            if request is None:
                state.profile_version += 1
        return result

    async def update_ui_profile(self, request, schema, settings, today):
        """UI workflow stand-in with the same validation and atomic commit as Postgres."""
        from uuid import uuid4

        from app.db.repository import StaleProfileError
        from app.models.match import Invalidation
        from app.models.profile import AppliedDecision, DecisionAction
        from app.models.update import ChangeKind, UpdateResult
        from app.profile.update import UpdateError, check_request

        cid = request.company_id
        state = self.states.get(cid)
        if state is None or state.state not in EMBEDDABLE:
            raise StateError("profile is not confirmed")
        busy = await self.company_busy(cid, settings.step_lock_timeout_s)
        if busy:
            raise BusyError(busy)
        if state.profile_version != request.profile_version:
            raise StaleProfileError("profile version is stale")
        errors = check_request(request, schema)
        if errors:
            raise UpdateError(errors)
        draft = self.published[cid].model_copy(deep=True)
        audit, changed = [], set()
        update_id = uuid4()
        for change in request.changes:
            spec = schema.field(change.field)
            field = draft.field(change.field)
            old = field.chosen
            row_id, row_key = change.row_id, change.key
            if spec.is_list:
                if change.kind is ChangeKind.ADD:
                    field.items.append(
                        FieldResult(
                            name=change.field,
                            status=FieldStatus.SINGLE_SOURCE,
                            key={k: change.cells.get(k) for k in spec.key or []},
                            value=change.cells,
                        )
                    )
                    target = field.items[-1]
                    row_id = len(field.items)
                    old = None
                else:
                    target = next(
                        (
                            item
                            for n, item in enumerate(field.items, 1)
                            if not item.dropped
                            and (
                                (row_id is not None and n == row_id)
                                or (row_key is not None and item.key == row_key)
                            )
                        ),
                        None,
                    )
                    if target is None:
                        raise NotFoundError("profile row not found")
                    old = target.chosen
                    row_id = field.items.index(target) + 1
                row_key = target.key
                new = None if change.kind is ChangeKind.REMOVE else {**(old or {}), **change.cells}
                action = DecisionAction.DROP if new is None else DecisionAction.EDIT
                target.decision = AppliedDecision(
                    action=action, value=new, typed=True, text=f"{ADVISOR_SOURCE} {today}"
                )
                changed.add(f"{spec.table}:{row_id}")
            else:
                new = change.value
                field.decision = AppliedDecision(
                    action=DecisionAction.VALUE,
                    value=new,
                    typed=True,
                    text=f"{ADVISOR_SOURCE} {today}",
                )
            audit.append(
                AuditEntry(
                    kind=AuditKind.PROFILE_CHANGED,
                    company_id=cid,
                    acted_by=request.changed_by,
                    acted_at=datetime.now(UTC),
                    update_id=update_id,
                    table_name=spec.table,
                    field=change.field,
                    row_id=row_id,
                    row_key=row_key,
                    change_kind=change.kind,
                    old_value=old,
                    new_value=new,
                    reason=request.reason,
                    profile_version=state.profile_version + 1,
                    screened_at=datetime.now(UTC)
                    if change.field == "ansprechpartner_email"
                    else None,
                )
            )
        stale = any(c.field != "ansprechpartner_email" for c in request.changes)
        self.published[cid] = draft
        state.profile_version += 1
        state.embeddings_stale = state.embeddings_stale or (
            stale and state.state is not ProjectState.CONFIRMED
        )
        for entry in audit:
            await self.append_audit(entry)
        if stale:
            for (company, _), match in self.matches.items():
                if company == cid:
                    match.is_current = False
                    match.invalidation_reason = Invalidation.PROFILE_CHANGED
                    self._sync_alert(match)
        return UpdateResult(
            company_id=cid,
            update_id=update_id,
            profile_version=state.profile_version,
            changes=len(request.changes),
            changed_features=sorted(changed),
            embeddings_stale=state.embeddings_stale,
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

    # Background jobs -------------------------------------------------------------------

    async def create_job(self, job: Job) -> Job:
        active = next(
            (j for j in self.jobs.values() if j.dedupe_key == job.dedupe_key and j.status.active),
            None,
        )
        if active is not None:
            raise BusyError(f"job {active.id} ({active.operation}) is already active; wait for it")
        self.jobs[job.id] = job.model_copy(deep=True)
        return job.model_copy(deep=True)

    async def save_job(self, job: Job) -> None:
        if job.id not in self.jobs:
            raise NotFoundError(f"no job with id {job.id}")
        self.jobs[job.id] = job.model_copy(deep=True)

    async def get_job(self, job_id: str) -> Job | None:
        job = self.jobs.get(job_id)
        return job.model_copy(deep=True) if job else None

    async def list_jobs(
        self, company_id: str | None = None, *, retry_of: str | None = None
    ) -> list[Job]:
        jobs = [
            j
            for j in self.jobs.values()
            if (company_id is None or j.company_id == company_id)
            and (retry_of is None or j.retry_of == retry_of)
        ]
        newest = sorted(jobs, key=lambda j: (j.created_at, j.id), reverse=True)
        return [j.model_copy(deep=True) for j in newest]

    async def interrupt_active_jobs(self, error: str) -> int:
        active = [j for j in self.jobs.values() if j.status.active]
        for job in active:
            job.status, job.error = JobStatus.INTERRUPTED, error
            job.finished_at = datetime.now(UTC)
        return len(active)

    async def clear_step_markers(self) -> int:
        marked = [s for s in self.states.values() if s.running_step]
        for state in marked:
            state.running_step = state.running_since = None
        return len(marked)

    async def company_busy(self, company_id: str, timeout_s: float) -> str | None:
        job = next(
            (j for j in self.jobs.values() if j.company_id == company_id and j.status.active),
            None,
        )
        if job is not None:
            return f"job {job.id} ({job.operation}) is {job.status}"
        state = self.states.get(company_id)
        running = state and state.running_step and state.running_since
        if running and (datetime.now(UTC) - state.running_since).total_seconds() < timeout_s:
            return f"{state.running_step} is running"
        return None


def _citation(result: FieldResult) -> str | None:
    """Same citation text the Postgres writer stores in `fundstelle`."""
    if result.entered_by_advisor:
        return ADVISOR_SOURCE
    group = result.chosen_group
    if group is None or not group.candidates:
        return None
    best = max(group.candidates, key=lambda c: c.confidence)
    return f"{best.source_filename} S. {best.source_page}"


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
