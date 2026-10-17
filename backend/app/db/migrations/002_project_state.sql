-- Project state shared by steps 8 to 10 (confirm, embed, clean up). One row per project
-- (company_id = profil.deal.projektname). States only move forward:
-- published -> confirmed -> embedded -> cleaned. A failed step leaves the state unchanged
-- and stores the error text, so it is safe to repeat. Idempotent; applied by --init-db.
-- Never deleted by step 10: it protects a confirmed profile from being overwritten.

CREATE TABLE IF NOT EXISTS pipeline.project_state (
    company_id           text PRIMARY KEY,
    state                text NOT NULL
                         CHECK (state IN ('published', 'confirmed', 'embedded', 'cleaned')),
    published_at         timestamptz,
    published_docs       jsonb NOT NULL DEFAULT '[]'::jsonb,  -- doc_ids of the last publish
    confirmed_by         text,                                -- plain name, no login yet
    confirmed_at         timestamptz,
    draft_version        text,                                -- draft the confirmation used
    embedded_at          timestamptz,
    embed_model          text,
    embeddings_expected  integer,
    embeddings_written   integer,
    embeddings_fallback  integer,                             -- deterministic fallback sentences
    expected_features    jsonb NOT NULL DEFAULT '[]'::jsonb,  -- ["finding:12", ...]
    cleaned_at           timestamptz,
    last_error           text,
    updated_at           timestamptz NOT NULL DEFAULT now()
);
