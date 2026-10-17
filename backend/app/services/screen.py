"""Steps 13 to 15 service: screen companies against stored laws and store the matches.

Rule filter (stage 1), max cosine score (stage 2), LLM judge (stage 3); each stage narrows
the set. Only companies in state embedded or cleaned with fresh vectors are screened. A
section already judged with an unchanged feature sentence is skipped (no LLM call) unless
`force`. A dry run stops after stage 2 and stores nothing (the calibration tool).
"""

import asyncio
import logging
from time import perf_counter

from openai import AsyncOpenAI

from app.config import Settings
from app.db.repository import Repository, StateError
from app.matching.judge import Judge, JudgeOutcome
from app.matching.rules import filter_pairs
from app.matching.score import MatchError, rank_sections
from app.models.law import Law, LawSection
from app.models.match import CompanyFeatures, Match, ScreenReport, SectionScore, Trigger
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


def needs_judging(stored: Match | None, sentences: dict[str, str]) -> bool:
    """New section, or the stored pair's feature is gone or has a new sentence."""
    return stored is None or sentences.get(stored.feature_ref) != stored.feature_text


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
        sections = await self.repository.list_law_sections(law_id)
        if company_id is not None:
            return [await self.screen_company(company_id, laws, sections, dry_run, force, trigger)]
        reports = []
        for state in await self.repository.list_project_states():
            try:
                reports.append(
                    await self.screen_company(
                        state.company_id, laws, sections, dry_run, force, trigger
                    )
                )
            except StateError as exc:
                reports.append(
                    ScreenReport(company_id=state.company_id, status="refused", message=str(exc))
                )
        return reports

    async def screen_pending(self, company_id: str) -> ScreenReport:
        """Re-screen after advisor updates: only changed or new features cost judge calls;
        the audit entries get `screened_at` once every section was judged."""
        pending = await self.repository.pending_profile_changes(company_id)
        if not pending:
            return ScreenReport(
                company_id=company_id,
                trigger=Trigger.PROFIL_UPDATE,
                status="nothing_to_do",
                message="no profile change waits for screening",
            )
        (report,) = await self.screen(company_id, trigger=Trigger.PROFIL_UPDATE)
        if not report.unjudged:
            await self.repository.mark_screened(pending)
        return report

    async def screen_company(
        self,
        company_id: str,
        laws: dict[int, Law],
        sections: list[LawSection],
        dry_run: bool = False,
        force: bool = False,
        trigger: Trigger = Trigger.LAW,
    ) -> ScreenReport:
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
                await self._judge(company, candidates, report, force, state.profile_version)
        finally:
            if not dry_run:
                await self.repository.end_step(company_id)
        report.seconds = perf_counter() - start
        log.info(
            "%s; project=%s pairs=%d skipped=%d unjudged=%d dry_run=%s",
            report.headline(),
            company_id,
            report.pairs,
            report.skipped,
            len(report.unjudged),
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
    ) -> None:
        """Stage 3 for the sections that need it, then store the judged pairs."""
        previous = await self.repository.list_matches(company.company_id)
        stored = {m.law_section_id: m for m in previous}
        sentences = {f.ref: f.text_basis for f in company.features}
        todo = [
            s for s in candidates if force or needs_judging(stored.get(s.section.id), sentences)
        ]
        report.skipped = len(candidates) - len(todo)
        judge = Judge(self.settings, self.client, self.slots)
        outcomes = await asyncio.gather(*(judge.judge_section(s) for s in todo))
        matches = []
        for candidate, outcome in zip(todo, outcomes, strict=True):
            report.input_tokens += outcome.tokens.input_tokens
            report.output_tokens += outcome.tokens.output_tokens
            if outcome.unjudged:
                report.unjudged.append(candidate.section.section_ref)
                continue
            matches.append(
                self._match(company, candidate, outcome, report.trigger, profile_version)
            )
        await self.repository.save_matches(matches)
        report.judged = report.stored = len(matches)
        report.hits = sum(m.judge_relevant for m in matches)

    def _match(
        self,
        company: CompanyFeatures,
        candidate: SectionScore,
        outcome: JudgeOutcome,
        trigger: Trigger,
        profile_version: int | None,
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
        )
