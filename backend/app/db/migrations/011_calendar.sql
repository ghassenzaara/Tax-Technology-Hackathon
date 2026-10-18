-- Calendar: the client's reply to a sent email, recorded by hand by the advisor. Accepted
-- replies carry the date of the Besprechung, rejected ones none. One reply per email; a new
-- recording replaces it (the audit log keeps every version). Idempotent and additive.
CREATE TABLE IF NOT EXISTS screening.outreach_response (
    outreach_id  bigint PRIMARY KEY REFERENCES screening.outreach (id) ON DELETE CASCADE,
    company_id   text NOT NULL,
    outcome      text NOT NULL CHECK (outcome IN ('accepted', 'rejected')),
    received_on  date NOT NULL,
    meeting_at   timestamptz,
    note         text,
    recorded_at  timestamptz NOT NULL DEFAULT now(),
    CONSTRAINT outreach_response_meeting CHECK ((outcome = 'accepted') = (meeting_at IS NOT NULL))
);
CREATE INDEX IF NOT EXISTS outreach_response_meeting_at
    ON screening.outreach_response (meeting_at) WHERE meeting_at IS NOT NULL;
