-- Background jobs of the HTTP API and matching freshness metadata (UI readiness fixes).
-- Idempotent and additive: profiles, sources, matches, alerts, audit and outreach are kept.

-- Jobs: persisted before they are scheduled, never resumed automatically after a restart
-- (the server marks them interrupted; the UI retries explicitly). Not a working table:
-- step 10 keeps them as history. `private` holds server-side inputs only.
CREATE TABLE IF NOT EXISTS pipeline.jobs (
    id           text PRIMARY KEY,
    operation    text NOT NULL CHECK (operation IN ('law_ingest', 'screen', 'advance', 'refresh')),
    company_id   text,
    status       text NOT NULL
                 CHECK (status IN ('queued', 'running', 'succeeded', 'failed', 'interrupted')),
    dedupe_key   text NOT NULL,
    params       jsonb NOT NULL DEFAULT '{}'::jsonb,
    private      jsonb NOT NULL DEFAULT '{}'::jsonb,
    result       jsonb,
    error        text,
    retry_of     text REFERENCES pipeline.jobs (id),
    created_at   timestamptz NOT NULL DEFAULT now(),
    started_at   timestamptz,
    finished_at  timestamptz
);
-- One active job per company (or per law content): duplicate submissions are refused.
CREATE UNIQUE INDEX IF NOT EXISTS jobs_one_active ON pipeline.jobs (dedupe_key)
    WHERE status IN ('queued', 'running');
CREATE INDEX IF NOT EXISTS jobs_company ON pipeline.jobs (company_id, created_at);

-- Match freshness. A decision is current only while its judge inputs still apply; an
-- invalidated decision keeps its historical verdict. Rows stored before this migration get
-- no guessed fingerprint: they stay non-current until one explicit screening pass.
ALTER TABLE screening.match ADD COLUMN IF NOT EXISTS is_current boolean NOT NULL DEFAULT false;
ALTER TABLE screening.match ADD COLUMN IF NOT EXISTS input_fingerprint text;
ALTER TABLE screening.match ADD COLUMN IF NOT EXISTS invalidation_reason text;
UPDATE screening.match SET invalidation_reason = 'migrated'
WHERE NOT is_current AND input_fingerprint IS NULL AND invalidation_reason IS NULL;
CREATE INDEX IF NOT EXISTS match_company_current ON screening.match (company_id, is_current);

-- Screening completeness per company and law: deferred or failed sections keep it
-- incomplete until a later explicit screening request finishes the remaining work.
CREATE TABLE IF NOT EXISTS screening.coverage (
    company_id       text NOT NULL,
    law_id           bigint NOT NULL REFERENCES screening.law (id) ON DELETE CASCADE,
    complete         boolean NOT NULL,
    deferred         integer NOT NULL DEFAULT 0,
    unjudged         integer NOT NULL DEFAULT 0,
    profile_version  integer,
    screened_at      timestamptz NOT NULL DEFAULT now(),
    PRIMARY KEY (company_id, law_id)
);
