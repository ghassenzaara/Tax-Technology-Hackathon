-- Working data of the extraction pipeline (steps 5 to 7), kept apart from the teammate's
-- profile schema `profil` (backend/db_scheme). Deleted after extraction and embedding
-- (step 10). Idempotent; applied by `uv run python -m app.cli --init-db`.
-- company_id is the deal's project name (profil.deal.projektname).

CREATE SCHEMA IF NOT EXISTS pipeline;

-- Append-only per run; one document's rows are replaced in one transaction on rerun.
CREATE TABLE IF NOT EXISTS pipeline.field_candidates (
    id               bigserial PRIMARY KEY,
    company_id       text NOT NULL,
    field_name       text NOT NULL,
    value            jsonb NOT NULL,       -- scalar value or, for list fields, one row as object
    evidence         text NOT NULL,
    source_doc       text NOT NULL,
    source_filename  text NOT NULL,
    source_page      integer NOT NULL CHECK (source_page >= 1),
    confidence       double precision NOT NULL CHECK (confidence BETWEEN 0 AND 1),
    needs_review     boolean NOT NULL DEFAULT false,
    chunk_index      integer NOT NULL DEFAULT 0,
    created_at       timestamptz NOT NULL DEFAULT now()
);
CREATE INDEX IF NOT EXISTS field_candidates_company_doc
    ON pipeline.field_candidates (company_id, source_doc);

CREATE TABLE IF NOT EXISTS pipeline.document_extractions (
    company_id      text NOT NULL,
    doc_id          text NOT NULL,
    filename        text NOT NULL,
    status          text NOT NULL,
    warnings        jsonb NOT NULL DEFAULT '[]'::jsonb,
    chunks          integer NOT NULL DEFAULT 0,
    repair_used     boolean NOT NULL DEFAULT false,
    input_tokens    integer NOT NULL DEFAULT 0,
    output_tokens   integer NOT NULL DEFAULT 0,
    agent_seconds   double precision NOT NULL DEFAULT 0,
    document_class  text,                  -- profil.dokument_klasse.code
    document_title  text,
    document_date   date,
    created_at      timestamptz NOT NULL DEFAULT now(),
    PRIMARY KEY (company_id, doc_id)
);

-- Merge result as reviewed in step 8; published rows land in schema profil.
CREATE TABLE IF NOT EXISTS pipeline.profile_drafts (
    id          bigserial PRIMARY KEY,
    company_id  text NOT NULL,
    draft       jsonb NOT NULL,
    created_at  timestamptz NOT NULL DEFAULT now()
);
CREATE INDEX IF NOT EXISTS profile_drafts_company
    ON pipeline.profile_drafts (company_id, created_at);

-- Projects the pipeline published into profil. Only these may be replaced by a later
-- publish, so seeds and hand-made profiles with the same project name stay untouched.
CREATE TABLE IF NOT EXISTS pipeline.publications (
    company_id    text PRIMARY KEY,
    published_at  timestamptz NOT NULL DEFAULT now()
);
