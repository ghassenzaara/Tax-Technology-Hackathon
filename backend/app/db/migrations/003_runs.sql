-- Runs of the upload layer (step 0): one row per execution of steps 1 to 7 plus publish for
-- a company, and one progress row per discovered file. File names, counts and warnings
-- only, never document text. Step 10 deletes the company's run_documents and clears the
-- folder of its runs; the runs stay as history. Idempotent; applied by --init-db and at
-- API startup.

CREATE TABLE IF NOT EXISTS pipeline.runs (
    run_id        text PRIMARY KEY,
    company_id    text NOT NULL,
    project_name  text,                   -- as typed in the UI (company_id is its slug)
    state         text NOT NULL
                  CHECK (state IN ('queued', 'running', 'finished', 'failed', 'interrupted')),
    folder        text,                   -- upload folder; cleared by step 10
    started_by    text NOT NULL,          -- upload | rerun
    created_at    timestamptz NOT NULL DEFAULT now(),
    started_at    timestamptz,
    finished_at   timestamptz,
    counts        jsonb NOT NULL DEFAULT '{}'::jsonb,  -- documents per final status
    published     boolean NOT NULL DEFAULT false,
    last_error    text
);
CREATE INDEX IF NOT EXISTS runs_company ON pipeline.runs (company_id, created_at);

CREATE TABLE IF NOT EXISTS pipeline.run_documents (
    run_id             text NOT NULL REFERENCES pipeline.runs (run_id) ON DELETE CASCADE,
    company_id         text NOT NULL,
    relative_path      text NOT NULL,
    doc_id             text,
    parse_status       text NOT NULL DEFAULT 'pending',
    extraction_status  text NOT NULL DEFAULT 'pending',
    warnings           jsonb NOT NULL DEFAULT '[]'::jsonb,
    pages              integer,
    seconds            double precision,
    input_tokens       integer NOT NULL DEFAULT 0,
    output_tokens      integer NOT NULL DEFAULT 0,
    updated_at         timestamptz NOT NULL DEFAULT now(),
    PRIMARY KEY (run_id, relative_path)
);
CREATE INDEX IF NOT EXISTS run_documents_company ON pipeline.run_documents (company_id);
