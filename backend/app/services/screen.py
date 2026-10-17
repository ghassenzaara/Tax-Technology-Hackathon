"""Steps 13 to 15 service: screen companies against stored laws and store the matches.

Rule filter (stage 1), max cosine score (stage 2), LLM judge (stage 3); each stage narrows
the set. Only companies in state embedded or cleaned with fresh vectors are screened.

Every execution reconciles all earlier decisions of the company within its scope (one law
or all laws). A section above the threshold reuses its stored verdict when the judge inputs
are unchanged (input fingerprint, no LLM call) unless `force`; otherwise it is judged again.
At most `match_max_candidates` sections are judged per execution; the rest are deferred to
the next explicit request. Earlier decisions that no longer qualify (feature removed, rule
excluded, below the threshold) become non-current and their new/seen alerts obsolete;
deferred or failed ones stay non-current until judged. Decisions, alerts, coverage and the
screened audit markers change in one transaction. A dry run stops after stage 2 and changes
nothing (the calibration tool).
"""

import asyncio
import logging
from time import perf_counter

from openai import AsyncOpenAI

from app.config import Settings
from app.db.repository import BusyError, Repository, StateError
from app.matching.judge import Judge, JudgeOutcome, input_fingerprint
from app.matching.rules import filter_pairs
from app.matching.score import MatchError, rank_sections
from app.models.law import Law, LawSection
from app.models.match import (
    CompanyFeatures,
    Invalidation,
    Match,
    ScreeningChanges,
    ScreenReport,
    SectionScore,
    Trigger,
)
from app.models.project import ProjectState, ProjectStatus

log = logging.getLogger(__name__)

SCREENABLE = frozenset({ProjectState.EMBEDDED, ProjectState.CLEANED})


class ScreenError(RuntimeError):
    """Screening cannot run (for example the judge needs an API key)."""


def check_state(company_id: str, state: ProjectStatus | None) -> None:
    """Refuse companies that are not embedded or whose vectors are stale."""
    if state is None or state.state not in SCREENABLE:
        current = state.state if state else "unknown"
        raise StateError(
            f"project '{company_id}' is {current}: screening needs state embedded or cleaned"
        )
    if state.embeddings_stale:
        raise StateError(
            f"project '{company_id}' has stale vectors after an update; run --advance first"
        )


def check_models(company: CompanyFeatures, sections: list[LawSection]) -> None:
    """Vectors of different models are never compared: a mismatch is an error."""
    feature_models = {f.modell for f in company.features}
    for model in sorted({s.model for s in sections}):
        if feature_models and model not in feature_models:
            raise MatchError(
                f"law sections use model {model}, the features of '{company.company_id}' use "
                f"{', '.join(sorted(feature_models))}; re-embed one side with the same model"
            )


class Screener:
    def __init__(self, repository: Repository, settings: Settings, client: AsyncOpenAI | None):
        self.repository = repository
        self.settings = settings
        self.client = client
        self.slots = asyncio.Semaphore(settings.judge_concurrency)

    async def screen(
        self,
        company_id: str | None = None,
        law_id: int | None = None,
        *,
        dry_run: bool = False,
        force: bool = False,
        trigger: Trigger = Trigger.LAW,
    ) -> list[ScreenReport]:
        """One company (refused with the reason) or every eligible company (others are
        reported as refused), against one law or all laws."""
        laws = {law.id: law for law in await self.repository.list_laws()}
        if law_id is not None and law_id not in laws:
            raise ScreenError(f"no law with id {law_id}")
        if law_id is not None:
            laws = {law_id: laws[law_id]}
        sections = await self.repository.list_law_sections(law_id)

        async def one(cid: str) -> ScreenReport:
            # A complete screening against every law also covers pending profile changes.
            pending = (
                [] if law_id is not None else await self.repository.pending_profile_changes(cid)
            )
            return await self.screen_company(
                cid, laws, sections, dry_run, force, trigger, screened_entries=pending
            )

        if company_id is not None:
            return [await one(company_id)]
        reports = []
        for state in await self.repository.list_project_states():
            try:
                reports.append(await one(state.company_id))
            except (StateError, BusyError) as exc:
                reports.append(
                    ScreenReport(company_id=state.company_id, status="refused", message=str(exc))
                )
            except Exception as exc:  # one company's problem never stops the others
                log.warning("screening of %s failed: %s", state.company_id, type(exc).__name__)
                reports.append(
                    ScreenReport(
                        company_id=state.company_id,
                        status="failed",
                        message=f"{type(exc).__name__}: {exc}",
                    )
                )
        return reports

    async def screen_pending(self, company_id: str) -> ScreenReport:
        """Re-screen after advisor updates, or finish deferred and failed work: only changed
        judge inputs cost calls. The audit entries get `screened_at` in the reconciliation's
        transaction, and only once every candidate section has a current decision."""
        pending = await self.repository.pending_profile_changes(company_id)
        status = await self.repository.screening_status(company_id)
        if not (pending or status.incomplete_laws or status.stale_decisions):
            return ScreenReport(
                company_id=company_id,
                trigger=Trigger.PROFIL_UPDATE,
                status="nothing_to_do",
                message="no profile change waits for screening",
            )
        laws = {law.id: law for law in await self.repository.list_laws()}
        sections = await self.repository.list_law_sections()
        return await self.screen_company(
            company_id, laws, sections, trigger=Trigger.PROFIL_UPDATE, screened_entries=pending
        )

    async def screen_company(
        self,
        company_id: str,
        laws: dict[int, Law],
        sections: list[LawSection],
        dry_run: bool = False,
        force: bool = False,
        trigger: Trigger = Trigger.LAW,
        screened_entries: list[int] = (),
    ) -> ScreenReport:
        """`laws` is the scope: every earlier decision on these laws is reconciled."""
        start = perf_counter()
        state = await self.repository.get_project_state(company_id)
        check_state(company_id, state)
        if not dry_run and self.client is None:
            raise ScreenError("the judge needs OPENAI_API_KEY (use --dry-run without it)")
        if not dry_run:  # same per-company lock as embed and update
            await self.repository.begin_step(
                company_id, "screen", self.settings.step_lock_timeout_s
            )
        try:
            company = await self.repository.company_features(company_id)
            check_models(company, sections)
            report = ScreenReport(
                company_id=company_id,
                dry_run=dry_run,
                trigger=trigger,
                features=len(company.features),
                sections=len(sections),
            )
            rules = filter_pairs(company, laws, sections)  # stage 1
            report.removed, report.pairs = rules.removed, len(rules.pairs)
            report.scored = rank_sections(rules.pairs, laws, rules.form_unknown, self.settings)
            candidates = [s for s in report.scored if s.passed]  # stage 2
            report.candidates = len(candidates)
            if not dry_run:
                changes = await self._judge(
                    company, candidates, report, force, state.profile_version, sorted(laws)
                )
                if report.complete:
                    changes.screened_entries = list(screened_entries)
                await self.repository.apply_screening(changes)
        finally:
            if not dry_run:
                await self.repository.end_step(company_id)
        report.seconds = perf_counter() - start
        log.info(
            "%s; project=%s pairs=%d skipped=%d unjudged=%d deferred=%d invalidated=%d dry_run=%s",
            report.headline(),
            company_id,
            report.pairs,
            report.skipped,
            len(report.unjudged),
            len(report.deferred),
            report.invalidated,
            dry_run,
        )
        return report

    async def _judge(
        self,
        company: CompanyFeatures,
        candidates: list[SectionScore],
        report: ScreenReport,
        force: bool,
        profile_version: int | None,
        law_ids: list[int],
    ) -> ScreeningChanges:
        """Stage 3 for the sections whose inputs changed, within the judge budget, plus
        the reconciliation of every earlier decision in scope."""
        previous = {
            m.law_section_id: m
            for m in await self.repository.list_matches(company.company_id, include_history=True)
            if m.law_id in law_ids
        }
        reused, todo = [], []
        for candidate in candidates:  # highest score first
            fingerprint = input_fingerprint(candidate, self.settings)
            stored = previous.get(candidate.section.id)
            if not force and stored is not None and stored.input_fingerprint == fingerprint:
                reused.append(candidate.section.id)
            else:
                todo.append((candidate, fingerprint))
        budget = self.settings.match_max_candidates  # cached sections cost no budget
        todo, deferred = todo[:budget], todo[budget:]
        report.skipped = len(reused)
        report.deferred = [c.section.section_ref for c, _ in deferred]

        judge = Judge(self.settings, self.client, self.slots)
        outcomes = await asyncio.gather(*(judge.judge_section(c) for c, _ in todo))
        open_work = {law_id: [0, 0] for law_id in law_ids}  # law -> deferred, unjudged
        matches, invalidated = [], {}
        for (candidate, fingerprint), outcome in zip(todo, outcomes, strict=True):
            report.input_tokens += outcome.tokens.input_tokens
            report.output_tokens += outcome.tokens.output_tokens
            if outcome.unjudged:
                report.unjudged.append(candidate.section.section_ref)
                invalidated[candidate.section.id] = Invalidation.JUDGE_FAILED
                open_work[candidate.law.id][1] += 1
                continue
            matches.append(
                self._match(
                    company, candidate, outcome, report.trigger, profile_version, fingerprint
                )
            )
        for candidate, _ in deferred:
            invalidated[candidate.section.id] = Invalidation.DEFERRED
            open_work[candidate.law.id][0] += 1
        for section_id in previous.keys() - {c.section.id for c in candidates}:
            invalidated[section_id] = Invalidation.NOT_CANDIDATE  # no longer qualifies

        # Only earlier decisions change; an unchanged invalidation is no update.
        invalidated = {
            sid: reason
            for sid, reason in invalidated.items()
            if sid in previous
            and (previous[sid].is_current or previous[sid].invalidation_reason != reason)
        }
        report.judged = report.stored = len(matches)
        report.hits = sum(m.judge_relevant for m in matches)
        report.invalidated = sum(previous[sid].is_current for sid in invalidated)
        return ScreeningChanges(
            company_id=company.company_id,
            law_ids=law_ids,
            profile_version=profile_version,
            judged=matches,
            reused=reused,
            invalidated=invalidated,
            coverage={law_id: (d, u) for law_id, (d, u) in open_work.items()},
        )

    def _match(
        self,
        company: CompanyFeatures,
        candidate: SectionScore,
        outcome: JudgeOutcome,
        trigger: Trigger,
        profile_version: int | None,
        fingerprint: str,
    ) -> Match:
        pair, verdict = outcome.pair, outcome.verdict
        return Match(
            company_id=company.company_id,
            gesellschaft_id=company.gesellschaft_id,
            law_id=candidate.law.id,
            law_section_id=candidate.section.id,
            objekt_typ=pair.feature.objekt_typ,
            objekt_id=pair.feature.objekt_id,
            feature_text=pair.feature.text_basis,
            score=round(pair.score, 4),
            norm_match=pair.norm_match,
            form_unknown=candidate.form_unknown,
            judge_relevant=verdict.relevant,
            judge_direction=verdict.direction,
            judge_reason=verdict.reason.strip(),
            judge_confidence=verdict.confidence,
            model=self.settings.openai_model,
            trigger=trigger,
            profile_version=profile_version,
            is_current=True,
            input_fingerprint=fingerprint,
        )
