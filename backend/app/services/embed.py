"""Step 9 service: one descriptive sentence and one vector per feature of a confirmed profile.

Reads the confirmed profile (never the draft), selects the features, writes the sentences
in parallel (semaphore, failures isolated per feature with a fallback sentence), embeds
them in batches, then replaces the company's vectors in one transaction. The state becomes
embedded only when the stored set equals the expected set (the gate for step 10). Any
embedding failure fails the whole step and leaves the state unchanged.

After an advisor update (plan 4b.5) `refresh` works by diff: only changed and new features
get a new sentence, all metadata is recomputed, a cleaned project stays cleaned.
"""

import asyncio
import logging
from time import perf_counter

from openai import AsyncOpenAI

from app.config import Settings
from app.db.repository import Repository, StateError
from app.features.select import select_features
from app.features.sentences import SentenceWriter
from app.llm import call_with_retry
from app.models.features import EmbeddingRecord, EmbedReport, Feature
from app.models.project import EMBEDDABLE, ProjectState
from app.profile.schema import ProfileSchema

log = logging.getLogger(__name__)


class EmbedError(RuntimeError):
    """The embedding step failed; the project state is unchanged."""


class Embedder:
    def __init__(
        self,
        repository: Repository,
        schema: ProfileSchema,
        settings: Settings,
        client: AsyncOpenAI,
    ):
        self.repository = repository
        self.schema = schema
        self.settings = settings
        self.client = client
        self.slots = asyncio.Semaphore(settings.embed_concurrency)
        self.sentences = SentenceWriter(settings, client, self.slots)

    async def run(self, company_id: str) -> EmbedReport:
        """Embed every feature of the company with a new sentence; rerunning replaces the
        vectors. Also the repair after a failed refresh (stale vectors)."""
        return await self._run(company_id, changed=None)

    async def refresh(self, company_id: str, changed: set[str]) -> EmbedReport:
        """After an advisor update (plan 4b.5), by diff: features in `changed` and new
        features get a new sentence and vector, features no longer expected lose theirs,
        all others keep their vector; metadata is recomputed for every feature without
        an LLM call. Nothing to do before the first embedding."""
        state = await self.repository.get_project_state(company_id)
        if state is not None and state.state is ProjectState.CONFIRMED:
            return EmbedReport(company_id=company_id, model=self.settings.embedding_model)
        return await self._run(company_id, changed=changed)

    async def _run(self, company_id: str, changed: set[str] | None) -> EmbedReport:
        start = perf_counter()
        state = await self.repository.get_project_state(company_id)
        if state is None or state.state not in EMBEDDABLE:
            current = state.state if state else "not published"
            raise StateError(
                f"project '{company_id}' is {current}: embedding needs a confirmed profile"
            )
        model = self.settings.embedding_model
        report = EmbedReport(company_id=company_id, model=model)
        await self.repository.begin_step(company_id, "embed", self.settings.step_lock_timeout_s)
        try:
            snapshot = await self.repository.load_profile_snapshot(company_id, self.schema)
            features = select_features(snapshot, self.settings)
            report.expected = len(features)
            stored = {}
            if changed is not None:  # refresh: keep the vectors of untouched features
                stored = {
                    r.ref: r for r in await self.repository.list_embeddings(company_id, model)
                }
            fresh = [f for f in features if f.ref not in stored or f.ref in (changed or ())]
            written = await asyncio.gather(*(self.sentences.write(f) for f in fresh))
            vectors = await self._embed([text for text, _ in written])
            new = {
                f.ref: _record(f, model, vector, text)
                for f, (text, _), vector in zip(fresh, written, vectors, strict=True)
            }
            records = [
                new.get(f.ref) or _record(f, model, stored[f.ref].vektor, stored[f.ref].text_basis)
                for f in features
            ]
            report.reused = len(features) - len(fresh)
            report.fallback_features = [
                f.ref for f, (_, fallback) in zip(fresh, written, strict=True) if fallback
            ]
            report.fallback = len(report.fallback_features)
            report.sentences = {f.ref: text for f, (text, _) in zip(fresh, written, strict=True)}
            await self.repository.replace_embeddings(company_id, model, records, report.fallback)
        except Exception as exc:
            await self.repository.record_error(company_id, f"embed failed: {exc}")
            if isinstance(exc, StateError):
                raise
            raise EmbedError(f"embedding failed for '{company_id}': {exc}") from exc
        finally:
            await self.repository.end_step(company_id)
        report.written = len(records)
        report.seconds = perf_counter() - start
        log.info(
            "%s; project=%s model=%s expected=%d new=%d reused=%d fallback=%d",
            report.headline(),
            company_id,
            model,
            report.expected,
            len(fresh),
            report.reused,
            report.fallback,
        )
        return report

    async def _embed(self, texts: list[str]) -> list[list[float]]:
        """Vectors in input order; checks count, order and dimension of every batch."""
        size = self.settings.embedding_batch_size
        batches = [texts[i : i + size] for i in range(0, len(texts), size)]
        results = await asyncio.gather(*(self._embed_batch(b) for b in batches))
        return [vector for batch in results for vector in batch]

    async def _embed_batch(self, texts: list[str]) -> list[list[float]]:
        settings = self.settings
        response = await call_with_retry(  # transient errors are retried here
            lambda: self.client.embeddings.create(model=settings.embedding_model, input=texts),
            slots=self.slots,
            max_attempts=settings.agent_max_attempts,
            backoff_min_s=settings.agent_backoff_min_s,
            backoff_max_s=settings.agent_backoff_max_s,
        )
        data = sorted(getattr(response, "data", None) or [], key=lambda d: d.index)
        if [d.index for d in data] != list(range(len(texts))):
            raise EmbedError(f"expected {len(texts)} vectors, got {len(data)}")
        for d in data:
            if len(d.embedding) != settings.embedding_dimension:
                raise EmbedError(
                    f"vector dimension {len(d.embedding)}, expected {settings.embedding_dimension}"
                )
        return [list(d.embedding) for d in data]


def _record(feature: Feature, model: str, vector: list[float], text: str) -> EmbeddingRecord:
    return EmbeddingRecord(
        objekt_typ=feature.objekt_typ,
        objekt_id=feature.objekt_id,
        gesellschaft_id=feature.gesellschaft_id,
        steuerarten=feature.steuerarten,
        normen=feature.normen,
        jahre=feature.jahre,
        offen=feature.offen,
        modell=model,
        vektor=vector,
        text_basis=text,
    )
