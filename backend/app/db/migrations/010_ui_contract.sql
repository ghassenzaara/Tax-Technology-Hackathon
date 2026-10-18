-- UI contract layer (/api/ui, handout section 5.1): client data, advisor review per company
-- and law, read state of law changes, one email for several laws, new audit kinds and the
-- screening-run job. Idempotent and additive: profiles, sources, matches, alerts, outreach
-- and the audit log are kept.

-- Client data the frontend shows next to the profile (not a working table: step 10 keeps
-- it). The contact email stays in profil.gesellschaft.ansprechpartner_email. `citations`
-- keeps the file and page of every confirmed scalar value (fact id -> [{filename, page}]),
-- written once at confirmation: profil stores one citation per row, not per field.
CREATE TABLE IF NOT EXISTS pipeline.client_info (
    company_id          text PRIMARY KEY,
    customer_number     text,
    client_since        text,
    notes               text,
    contact_salutation  text,
    contact_name        text,
    contact_phone       text,
    citations           jsonb NOT NULL DEFAULT '{}'::jsonb,
    updated_at          timestamptz NOT NULL DEFAULT now()
);

-- The advisor's own review of one company and law change (the judge creates the match,
-- the advisor labels it; labels never change alerts). `pending_outreach_id` is set while a
-- send of this law is reserved and cleared when it is resolved.
CREATE TABLE IF NOT EXISTS screening.match_review (
    company_id           text NOT NULL,
    law_id               bigint NOT NULL REFERENCES screening.law (id) ON DELETE CASCADE,
    label                text NOT NULL DEFAULT 'unlabeled'
                         CHECK (label IN ('unlabeled', 'relevant', 'irrelevant')),
    email_status         text NOT NULL DEFAULT 'not_sent'
                         CHECK (email_status IN ('not_sent', 'sent', 'discussed')),
    notes                text,
    sent_at              timestamptz,
    outreach_id          bigint,
    discussed_at         timestamptz,
    updated_at           timestamptz,
    pending_outreach_id  bigint,
    PRIMARY KEY (company_id, law_id)
);

-- Read state of law changes (single advisor).
CREATE TABLE IF NOT EXISTS screening.law_seen (
    law_id   bigint PRIMARY KEY REFERENCES screening.law (id) ON DELETE CASCADE,
    seen_at  timestamptz NOT NULL DEFAULT now()
);

-- One email covering several laws: no single alert, the laws are listed instead.
ALTER TABLE screening.outreach ALTER COLUMN alert_id DROP NOT NULL;
ALTER TABLE screening.outreach ADD COLUMN IF NOT EXISTS law_ids bigint[] NOT NULL DEFAULT '{}';

-- New audit kinds. Template resets stay template_edited with new_value.reset = true.
ALTER TABLE pipeline.audit_log DROP CONSTRAINT IF EXISTS audit_log_kind_check;
ALTER TABLE pipeline.audit_log ADD CONSTRAINT audit_log_kind_check CHECK (kind IN (
    'profile_confirmed', 'profile_changed', 'email_sent', 'template_edited',
    'match_review_changed', 'note_changed', 'documents_added'));

-- The "Screening starten" job: ingest new source PDFs, then screen every company.
ALTER TABLE pipeline.jobs DROP CONSTRAINT IF EXISTS jobs_operation_check;
ALTER TABLE pipeline.jobs ADD CONSTRAINT jobs_operation_check CHECK (operation IN (
    'law_ingest', 'screen', 'advance', 'refresh', 'screening_run'));
