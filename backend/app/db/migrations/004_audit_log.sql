-- Audit log (plan section 4b.4): the single, permanent audit trail for the parts the
-- platform owns, for several advisors working on the same data. Step 10 never deletes it;
-- it holds profile values, template text and references only, never document text.
-- Append-only: a trigger rejects DELETE, TRUNCATE and every UPDATE except setting the
-- matching step's `screened_at` marker once. Idempotent; applied by --init-db.
-- TODO: ask the schema owner to host the audit log inside `profil`.

CREATE TABLE IF NOT EXISTS pipeline.audit_log (
    id               bigserial PRIMARY KEY,
    kind             text NOT NULL CHECK (kind IN
                     ('profile_confirmed', 'profile_changed', 'email_sent', 'template_edited')),
    company_id       text,                  -- empty for template edits
    update_id        uuid,                  -- shared by the entries of one update call
    acted_by         text NOT NULL,         -- typed name until login exists (not tamper-proof)
    acted_at         timestamptz NOT NULL DEFAULT now(),
    table_name       text,                  -- profile_changed: profil table
    field            text,                  -- profile_changed: profile schema field
    row_id           integer,               -- profile_changed: profil row id (list rows)
    row_key          jsonb,                 -- profile_changed: key values of the row
    change_kind      text CHECK (change_kind IN ('edit', 'add', 'remove', 'end')),
    old_value        jsonb,                 -- a row snapshot for add and remove
    new_value        jsonb,                 -- profile_confirmed: draft version and counts
    reason           text,
    profile_version  integer,               -- version the entry produced
    outreach_id      bigint,                -- email_sent: the outreach snapshot (steps 16 to 19)
    screened_at      timestamptz            -- profile_changed: set once matching re-screened it
);
CREATE INDEX IF NOT EXISTS audit_log_company ON pipeline.audit_log (company_id, acted_at);

CREATE OR REPLACE FUNCTION pipeline.audit_log_append_only() RETURNS trigger
LANGUAGE plpgsql AS $$
BEGIN
    IF TG_OP = 'UPDATE' THEN  -- nested: OLD does not exist for TRUNCATE
        IF OLD.screened_at IS NULL
           AND to_jsonb(NEW) - 'screened_at' = to_jsonb(OLD) - 'screened_at' THEN
            RETURN NEW;  -- the only allowed change: matching sets the screened marker once
        END IF;
    END IF;
    RAISE EXCEPTION 'pipeline.audit_log is append-only (% rejected)', TG_OP;
END $$;

DROP TRIGGER IF EXISTS audit_log_append_only ON pipeline.audit_log;
CREATE TRIGGER audit_log_append_only BEFORE UPDATE OR DELETE ON pipeline.audit_log
    FOR EACH ROW EXECUTE FUNCTION pipeline.audit_log_append_only();
DROP TRIGGER IF EXISTS audit_log_no_truncate ON pipeline.audit_log;
CREATE TRIGGER audit_log_no_truncate BEFORE TRUNCATE ON pipeline.audit_log
    FOR EACH STATEMENT EXECUTE FUNCTION pipeline.audit_log_append_only();

-- Profile version (1 at confirmation, +1 per update), the flag for vectors that are out of
-- date after an update, and the per-company busy marker of a running step.
ALTER TABLE pipeline.project_state ADD COLUMN IF NOT EXISTS profile_version integer;
ALTER TABLE pipeline.project_state
    ADD COLUMN IF NOT EXISTS embeddings_stale boolean NOT NULL DEFAULT false;
ALTER TABLE pipeline.project_state ADD COLUMN IF NOT EXISTS running_step text;
ALTER TABLE pipeline.project_state ADD COLUMN IF NOT EXISTS running_since timestamptz;
