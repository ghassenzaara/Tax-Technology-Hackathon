"""Step 5 and the validation half of step 6: one extraction agent for one document.

skip? -> chunk? -> LLM call (strict structured output) -> parse -> Pydantic validation
-> one repair retry -> semantic checks -> candidates. `extract` never raises: every
exception becomes a failed result, so one bad document never stops the others.
Code adds `source_doc`; the model only cites pages, so it cannot cite the wrong document.
"""

import asyncio
import json
import logging
from dataclasses import dataclass, field
from time import perf_counter
from typing import Any

from openai import AsyncOpenAI
from pydantic import BaseModel, ValidationError

from app.agents.chunking import Chunk, chunk_document
from app.agents.prompt import build_repair_message, build_system_prompt
from app.agents.verify import page_errors, verify_value
from app.config import Settings
from app.llm import call_with_retry, token_usage
from app.models.document import DocStatus, Document, Page, TokenUsage
from app.models.extraction import Candidate, DocumentExtraction, ExtractionStatus
from app.profile.schema import (
    DOCUMENT_KEY,
    FieldSpec,
    ProfileSchema,
    build_response_model,
    response_json_schema,
)

log = logging.getLogger(__name__)

MISSING_KEY_WARNING = "extraction not run: OPENAI_API_KEY is not set"


class InvalidOutput(Exception):
    """The model output stayed invalid after the repair retry."""


@dataclass
class _ChunkResult:
    candidates: list[Candidate] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)
    tokens: TokenUsage = field(default_factory=TokenUsage)
    repair_used: bool = False
    failed: bool = False
    dropped: bool = False  # at least one field was dropped by the semantic checks
    document: dict[str, Any] | None = None  # klasse_code, titel, dokument_datum


def skip_reason(doc: Document, settings: Settings) -> str | None:
    """Why no agent should run for this document (no LLM call, no cost), or None."""
    if doc.status in (DocStatus.FAILED, DocStatus.UNSUPPORTED):
        detail = "; ".join(doc.warnings)
        return f"document {doc.status}" + (f": {detail}" if detail else "")
    chars = sum(
        len("".join(page.text.split()))
        + sum(len("".join(cell.split())) for t in page.tables for row in t.rows for cell in row)
        for page in doc.pages
    )
    if chars < settings.min_extractable_chars:
        return f"only {chars} extractable characters (minimum {settings.min_extractable_chars})"
    return None


class Extractor:
    """Same prompt and schema for every document. `slots` caps requests in flight."""

    def __init__(
        self,
        schema: ProfileSchema,
        *,
        settings: Settings,
        client: AsyncOpenAI | None,
        slots: asyncio.Semaphore,
    ):
        self.schema = schema
        self.settings = settings
        self.client = client
        self.slots = slots
        self.response_model: type[BaseModel] = build_response_model(schema)
        self.system_prompt = build_system_prompt(schema, settings.evidence_max_chars)
        # TODO: confirm with one live call that gpt-6-luna accepts json_schema through Chat
        # Completions; if not, move this call to the Responses API (nothing else changes).
        self.response_format = {
            "type": "json_schema",
            "json_schema": {
                "name": "profile_extraction",
                "strict": True,
                "schema": response_json_schema(schema),
            },
        }

    async def extract(self, doc: Document, company_id: str) -> DocumentExtraction:
        start = perf_counter()
        try:
            result = await self._extract(doc, company_id)
        except Exception as exc:
            log.exception("unexpected error extracting %s", doc.relative_path)
            result = self._result(
                doc,
                company_id,
                ExtractionStatus.FAILED,
                warnings=[f"unexpected error: {type(exc).__name__}: {exc}"],
            )
        result.agent_s = perf_counter() - start
        return result

    async def _extract(self, doc: Document, company_id: str) -> DocumentExtraction:
        reason = skip_reason(doc, self.settings)
        if reason:
            return self._result(doc, company_id, ExtractionStatus.SKIPPED, warnings=[reason])
        if self.client is None:
            return self._result(
                doc, company_id, ExtractionStatus.FAILED, warnings=[MISSING_KEY_WARNING]
            )

        chunks, warnings = chunk_document(doc, self.settings)
        warnings = _parse_warnings(doc) + warnings
        results = await asyncio.gather(
            *(self._extract_chunk(doc, chunk, company_id) for chunk in chunks)
        )

        prefix = len(chunks) > 1
        for chunk, result in zip(chunks, results, strict=True):
            warnings += [f"chunk {chunk.index}: {w}" if prefix else w for w in result.warnings]
        failed = sum(r.failed for r in results)
        if failed == len(results):
            status = ExtractionStatus.FAILED
        elif failed or any(r.dropped for r in results) or doc.status is DocStatus.PARTIAL:
            status = ExtractionStatus.PARTIAL
        else:
            status = ExtractionStatus.OK
        document = next((r.document for r in results if r.document), None) or {}

        return self._result(
            doc,
            company_id,
            status,
            candidates=[] if status is ExtractionStatus.FAILED else _flatten(results),
            warnings=warnings,
            token_usage=sum((r.tokens for r in results), TokenUsage()),
            chunks=len(chunks),
            repair_used=any(r.repair_used for r in results),
            document_class=document.get("klasse_code"),
            document_title=document.get("titel"),
            document_date=document.get("dokument_datum"),
        )

    async def _extract_chunk(self, doc: Document, chunk: Chunk, company_id: str) -> _ChunkResult:
        pages = {page.page_no: page for page in chunk.pages}
        messages = [
            {"role": "system", "content": self.system_prompt},
            {"role": "user", "content": chunk.markdown},
        ]
        result = _ChunkResult()
        try:
            response = await self._call(messages)
            result.tokens += token_usage(response)
            content, parsed, errors = self._parse(response, pages)
            if errors:
                result.repair_used = True
                repair = [
                    *messages,
                    {"role": "assistant", "content": content or "(no output)"},
                    {"role": "user", "content": build_repair_message(errors)},
                ]
                response = await self._call(repair)
                result.tokens += token_usage(response)
                _, repaired, repair_errors = self._parse(response, pages)
                if repaired is not None:
                    parsed = repaired
                elif parsed is None:
                    raise InvalidOutput("; ".join(repair_errors))
                else:
                    result.warnings.append("repair output invalid; kept the first answer")
        except Exception as exc:
            log.warning(
                "extraction failed for %s chunk %d: %s", doc.relative_path, chunk.index, exc
            )
            result.failed = True
            result.warnings.append(f"extraction failed: {type(exc).__name__}: {exc}")
            return result

        result.document = getattr(parsed, DOCUMENT_KEY).model_dump(mode="json")
        for spec in self.schema.fields:
            entry = getattr(parsed, spec.name)
            if spec.is_list:
                entries = [(f"{spec.name}[{i}]", e) for i, e in enumerate(entry)]
            else:  # None = not stated in the document: no candidate row
                entries = [(spec.name, entry)] if entry is not None else []
            for label, e in entries:
                result.dropped |= self._add_candidate(
                    result, spec, label, e, doc, chunk, pages, company_id
                )
        return result

    def _add_candidate(
        self,
        result: _ChunkResult,
        spec: FieldSpec,
        label: str,
        entry: BaseModel,
        doc: Document,
        chunk: Chunk,
        pages: dict[int, Page],
        company_id: str,
    ) -> bool:
        """Verify one value or list item and append it. Returns True when it was dropped."""
        dumped = entry.model_dump(mode="json")
        if spec.is_list:
            names = [i.name for i in spec.items]
            raw = {n: getattr(entry, n) for n in names}
            value = {n: dumped[n] for n in names}
            if all(v is None for v in value.values()):
                result.warnings.append(f"{label}: dropped, entry has no values")
                return True
        else:
            raw, value = entry.value, dumped["value"]
        verified, warnings = verify_value(
            label,
            raw,
            entry.source_page,
            entry.evidence,
            entry.confidence,
            pages,
            self.settings,
        )
        result.warnings += warnings
        if verified is None:
            return True
        result.candidates.append(
            Candidate(
                company_id=company_id,
                field_name=spec.name,
                value=value,
                evidence=verified.evidence,
                source_doc=doc.doc_id,
                source_filename=doc.relative_path,
                source_page=verified.source_page,
                confidence=verified.confidence,
                needs_review=verified.needs_review,
                chunk_index=chunk.index,
            )
        )
        return False

    async def _call(self, messages: list[dict[str, Any]]) -> Any:
        settings = self.settings
        return await call_with_retry(
            lambda: self.client.chat.completions.create(
                model=settings.openai_model,
                messages=messages,
                response_format=self.response_format,
                reasoning_effort=settings.extraction_reasoning_effort,
            ),
            slots=self.slots,
            max_attempts=settings.agent_max_attempts,
            backoff_min_s=settings.agent_backoff_min_s,
            backoff_max_s=settings.agent_backoff_max_s,
        )

    def _parse(
        self, response: Any, pages: dict[int, Page]
    ) -> tuple[str | None, BaseModel | None, list[str]]:
        """(raw content, structurally valid result or None, errors for the repair message).

        A result can be valid and still carry errors: citations of pages that do not exist.
        """
        choices = getattr(response, "choices", None) or []
        if not choices:
            return None, None, ["empty response"]
        choice, message = choices[0], choices[0].message
        refusal = getattr(message, "refusal", None)
        if refusal:
            return None, None, [f"model refused: {refusal}"]
        content = message.content
        if getattr(choice, "finish_reason", None) == "length":
            return content, None, ["output was cut off at the length limit"]
        if not content or not content.strip():
            return content, None, ["empty response"]
        try:
            data = json.loads(content)
        except json.JSONDecodeError as exc:
            return content, None, [f"not valid JSON: {exc}"]
        try:
            parsed = self.response_model.model_validate(data)
        except ValidationError as exc:
            errors = [
                f"{'.'.join(str(part) for part in e['loc']) or '(root)'}: {e['msg']}"
                for e in exc.errors()
            ]
            return content, None, errors
        entries = {name: getattr(parsed, name) for name in self.schema.names}
        return content, parsed, page_errors(entries, set(pages))

    @staticmethod
    def _result(
        doc: Document, company_id: str, status: ExtractionStatus, **fields: Any
    ) -> DocumentExtraction:
        return DocumentExtraction(
            company_id=company_id,
            doc_id=doc.doc_id,
            filename=doc.relative_path,
            status=status,
            **fields,
        )


def _parse_warnings(doc: Document) -> list[str]:
    """Parse warnings of usable (partial) documents travel with the extraction result."""
    warnings = [f"parse: {w}" for w in doc.warnings]
    for page in doc.pages:
        warnings += [f"parse [{page.label}]: {w}" for w in page.warnings]
    return warnings


def _flatten(results: list[_ChunkResult]) -> list[Candidate]:
    return [candidate for result in results for candidate in result.candidates]
