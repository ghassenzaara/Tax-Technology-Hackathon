-- Steps 16 to 18 only. Calendar is deferred. These records survive working-data cleanup.
CREATE TABLE IF NOT EXISTS screening.alert (
    id bigserial PRIMARY KEY,
    match_id bigint UNIQUE REFERENCES screening.match(id) ON DELETE SET NULL,
    company_id text NOT NULL,
    status text NOT NULL DEFAULT 'new'
        CHECK (status IN ('new','seen','dismissed','actioned','obsolete')),
    dismissed_reason text,
    created_at timestamptz NOT NULL DEFAULT now(),
    status_changed_at timestamptz NOT NULL DEFAULT now(),
    -- Keep source history if a law is explicitly replaced (matching cascades its rows).
    source_snapshot jsonb NOT NULL
);
CREATE INDEX IF NOT EXISTS alert_company_status ON screening.alert(company_id, status);

CREATE TABLE IF NOT EXISTS screening.email_template (
    id integer PRIMARY KEY CHECK (id = 1),
    subject text NOT NULL,
    body text NOT NULL,
    updated_at timestamptz NOT NULL DEFAULT now()
);
-- Seeded from the configured signature by repository.initialize(), only if empty.

CREATE TABLE IF NOT EXISTS screening.outreach (
    id bigserial PRIMARY KEY,
    alert_id bigint NOT NULL REFERENCES screening.alert(id),
    company_id text NOT NULL,
    recipient text NOT NULL,
    delivered_to text NOT NULL,
    subject text NOT NULL,
    delivery_subject text NOT NULL,
    sent_body text NOT NULL,
    status text NOT NULL CHECK (status IN ('pending','sent','failed')),
    error text,
    provider_message_id text,
    created_at timestamptz NOT NULL DEFAULT now(),
    sent_at timestamptz,
    template_updated_at timestamptz NOT NULL
);
-- A durable reservation across processes; never expires after a crash.
CREATE UNIQUE INDEX IF NOT EXISTS outreach_one_delivery ON screening.outreach(alert_id)
    WHERE status IN ('pending','sent');

-- Backfill already-stored hits without resetting any existing alert status.
INSERT INTO screening.alert(match_id, company_id, source_snapshot)
SELECT m.id, m.company_id,
       jsonb_build_object('match', to_jsonb(m), 'law', to_jsonb(l),
           'section_ref', s.section_ref, 'section_text', s.text,
           'page_from', s.page_from, 'page_to', s.page_to)
FROM screening.match m JOIN screening.law l ON l.id = m.law_id
JOIN screening.law_section s ON s.id = m.law_section_id
WHERE m.judge_relevant
ON CONFLICT (match_id) DO NOTHING;
