"""The frontend's data types (frontend/src/data/types.ts, screening/contract v0.1.0) as
Pydantic models: camelCase JSON, optional fields left out instead of null (the contract
types them as strings, numbers or objects, never null). Values inside facts, rows and audit
entries are free JSON and keep their nulls."""

from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, model_serializer
from pydantic.alias_generators import to_camel

SCHEMA_VERSION = "0.1.0"
ADVISOR_ID = "advisor"  # single-user platform: the one Steuerberater


class UiModel(BaseModel):
    model_config = ConfigDict(
        alias_generator=to_camel, populate_by_name=True, serialize_by_alias=True
    )

    @model_serializer(mode="wrap")
    def _without_none(self, handler):
        nullable = {"value", "oldValue", "newValue", "old_value", "new_value"}
        return {k: v for k, v in handler(self).items() if v is not None or k in nullable}


class Source(UiModel):
    filename: str
    page: int
    quote: str | None = None  # review only; never after confirmation


class ValueWithSources(UiModel):
    value: Any
    sources: list[Source] = []


class Candidate(ValueWithSources):
    id: str
    alternatives: list[ValueWithSources] | None = None


class Fact(UiModel):
    id: str
    group: str | None = None
    label: str
    value: Any = None
    type: str
    enum_values: list[Any] | None = None
    unit: str | None = None
    code: str | None = None
    core_field: str | None = None
    sources: list[Source] = []
    review_status: str | None = None
    needs_review: bool | None = None
    review_reason: str | None = None
    reasoning: str | None = None
    candidates: list[Candidate] | None = None
    alternatives: list[ValueWithSources] | None = None


class Contact(UiModel):
    salutation: str | None = None
    name: str = ""
    email: str | None = None
    phone: str | None = None


class Company(UiModel):
    id: str
    advisor_id: str = ADVISOR_ID
    name: str
    status: str  # draft | active
    created_at: str
    updated_at: str
    confirmed_at: str | None = None
    confirmed_by: str | None = None
    last_dd_at: str | None = None
    contact: Contact = Contact()
    customer_number: str | None = None
    client_since: str | None = None
    legal_form: str | None = None
    industry: str | None = None
    facts: list[Fact] = []
    extra: dict[str, Any] | None = None
    notes: str | None = None


class LawSection(UiModel):
    id: str
    ref: str
    heading: str | None = None
    text: str


class LawBlock(UiModel):
    kind: str
    title: str
    text: str | None = None
    items: list[str] | None = None
    generated: bool | None = None


class LawTags(UiModel):
    tax_types: list[str] | None = None
    legal_forms: list[str] | None = None
    industries: list[str] | None = None
    norms: list[str] | None = None


class LawChange(UiModel):
    id: str
    title: str
    source_type: str
    source_name: str
    url: str = ""  # no fetched URL: the UI hides "Zur Quelle"
    published_at: str
    effective_at: str | None = None
    detected_at: str
    version: int = 1
    content_hash: str
    tags: LawTags | None = None
    sections: list[LawSection] = []
    blocks: list[LawBlock] = []
    extra: dict[str, Any] | None = None


class Evidence(UiModel):
    fact_id: str | None = None
    fact_label: str
    section_ref: str
    similarity: float | None = None


class MatchAssessment(UiModel):
    score: float
    confidence: float
    reason: str
    evidence: list[Evidence] = []
    law_version: int = 1
    screened_at: str
    run_id: str
    # Backend additions: a non-current decision waits for screening and cannot be sent.
    current: bool = True
    invalidation_reason: str | None = None
    direction: str | None = None


class MatchReview(UiModel):
    label: str = "unlabeled"
    email_status: str = "not_sent"
    notes: str | None = None
    sent_at: str | None = None
    outreach_id: str | None = None
    discussed_at: str | None = None
    updated_by: str | None = None
    updated_at: str | None = None
    send_pending: bool | None = None  # an earlier send of this law is unclear


class Match(UiModel):
    id: str
    company_id: str
    law_change_id: str
    assessment: MatchAssessment
    review: MatchReview


class Outreach(UiModel):
    id: str
    company_id: str
    match_ids: list[str]
    recipient: str
    subject: str
    body: str
    sent_at: str
    sent_by: str = ADVISOR_ID
    provider_message_id: str | None = None


class Advisor(UiModel):
    id: str = ADVISOR_ID
    name: str
    salutation: str | None = None
    title: str | None = None
    firm: str | None = None
    seen_law_change_ids: list[str] = []


class AuditEntry(UiModel):
    id: str
    company_id: str
    kind: str
    acted_by: str = ADVISOR_ID
    acted_at: str
    field: str | None = None
    old_value: Any = None
    new_value: Any = None
    match_id: str | None = None
    outreach_id: str | None = None
    summary: str | None = None


class ApiErrorBody(UiModel):
    code: str
    message: str


class UploadDocument(UiModel):
    id: str
    relative_path: str
    size_bytes: int | None = None
    file_type: str | None = None
    parsing_status: str
    extraction_status: str
    warnings: list[str] = []
    error: ApiErrorBody | None = None


class UploadRun(UiModel):
    schema_version: str = SCHEMA_VERSION
    id: str
    company_id: str
    status: str  # running | finished | failed
    started_at: str
    finished_at: str | None = None
    documents: list[UploadDocument] = []
    error: ApiErrorBody | None = None


class ProfileDraft(UiModel):
    schema_version: str = SCHEMA_VERSION
    company_id: str
    run_id: str
    created_at: str | None = None
    facts: list[Fact] = []
    draft_version: str


class EmailTemplate(UiModel):
    subject: str
    body: str


class StoredEmailTemplate(EmailTemplate):
    is_default: bool
    updated_at: str | None = None
    updated_by: str | None = None


class TemplateHistoryEntry(UiModel):
    id: str
    kind: str  # template_edited | template_reset
    acted_by: str = ADVISOR_ID
    acted_at: str
    old_subject: str
    old_body: str
    new_subject: str
    new_body: str


class Recovery(UiModel):
    available: bool
    action: str | None = None
    reason: str = ""


class Job(UiModel):
    """A background job (screening run, embedding, refresh) the UI polls."""

    id: str
    operation: str
    company_id: str | None = None
    status: str  # queued | running | succeeded | failed | interrupted
    error: str | None = None
    retry_of: str | None = None
    created_at: str
    finished_at: str | None = None
    result: dict[str, Any] | None = None


class Processing(UiModel):
    """Background work of one client after confirmation: what runs, what failed, and
    whether "Verarbeitung abschließen" (recover) would start something."""

    company_id: str
    state: str | None = None
    embeddings_stale: bool = False
    active_job: Job | None = None
    latest_job: Job | None = None
    recovery: Recovery
    screening_pending: bool = False
    update_pending: bool = False  # documents uploaded into the client wait for review


class ScreeningRunResult(UiModel):
    """Result of the "Screening starten" job."""

    new_law_change_ids: list[str] = []
    law_changes: int = 0
    companies_screened: int = 0
    affected_company_ids: list[str] = []
    hits: int = 0
    deferred: int = 0
    complete: bool = True


# Request bodies ------------------------------------------------------------------------


class CorePatch(UiModel):
    name: str | None = None
    legal_form: str | None = None
    industry: str | None = None
    customer_number: str | None = None
    client_since: str | None = None
    contact: Contact | None = None


class FactPatch(UiModel):
    id: str
    value: Any = None


class CompanyUpdate(UiModel):
    model_config = ConfigDict(extra="forbid")

    core: CorePatch | None = None
    facts: list[FactPatch] = []
    profile_version: int = Field(ge=1)


class NoteBody(UiModel):
    notes: str = Field(default="", max_length=20_000)


class Resolution(UiModel):
    kind: Literal["value", "left_empty"]
    value: Any = None
    candidate_id: str | None = None


class ConfirmBody(UiModel):
    resolutions: dict[str, Resolution] = {}
    draft_version: str


class MatchReviewPatch(UiModel):
    model_config = ConfigDict(extra="forbid")

    label: Literal["unlabeled", "relevant", "irrelevant"] | None = None
    email_status: Literal["not_sent", "sent", "discussed"] | None = None
    notes: str | None = None


class TemplateBody(UiModel):
    model_config = ConfigDict(extra="forbid")

    subject: str
    body: str


class SendEmailBody(UiModel):
    model_config = ConfigDict(extra="forbid")

    company_id: str
    recipient: str
    subject: str
    body: str
    match_ids: list[str] = Field(min_length=1)
    confirmed: Literal[True]
