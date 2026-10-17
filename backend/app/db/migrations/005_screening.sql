-- Steps 11 to 15: laws, their sections with vectors, and the screening matches. Own schema
-- `screening`: laws are company-independent and survive step 10 (which only deletes
-- `pipeline` working rows). Idempotent; applied by --init-db.
-- TODO: switch the vectors to vector(1536) with an HNSW index when the data grows.

CREATE SCHEMA IF NOT EXISTS screening;

CREATE TABLE IF NOT EXISTS screening.law (
    id            bigserial PRIMARY KEY,
    kind          text NOT NULL CHECK (kind IN
                  ('gesetz', 'gesetzentwurf', 'bmf_schreiben', 'verordnung', 'other')),
    title         text NOT NULL,
    reference     text,                              -- BGBl, Geschäftszeichen or draft number
    published_at  date,
    effective_at  date,
    status        text NOT NULL CHECK (status IN ('in_kraft', 'entwurf', 'angekuendigt')),
    legal_forms   text[] NOT NULL DEFAULT '{}',      -- empty = applies to all legal forms
    tax_types     text[] NOT NULL DEFAULT '{}',      -- values of profil.steuerart
    summary       text,
    filename      text NOT NULL,
    content_hash  text NOT NULL UNIQUE,              -- sha256 of the file: the duplicate guard
    ingested_at   timestamptz NOT NULL DEFAULT now()
);

CREATE TABLE IF NOT EXISTS screening.law_section (
    id           bigserial PRIMARY KEY,
    law_id       bigint NOT NULL REFERENCES screening.law(id) ON DELETE CASCADE,
    nr           integer NOT NULL,                   -- running number within the law
    section_ref  text NOT NULL,                      -- for example '§ 8c' or 'Rz. 12 bis Rz. 15'
    heading      text,
    page_from    integer,
    page_to      integer,
    text         text NOT NULL,
    norms        text[] NOT NULL DEFAULT '{}',       -- codes of profil.norm
    tax_types    text[] NOT NULL DEFAULT '{}',
    model        text NOT NULL,
    vector       real[] NOT NULL,                    -- same convention as profil.embedding
    UNIQUE (law_id, nr)
);

-- One stored pair per company and law section. The feature key is no foreign key: a
-- step 9 refresh replaces profil.embedding rows.
CREATE TABLE IF NOT EXISTS screening.match (
    id                bigserial PRIMARY KEY,
    company_id        text NOT NULL,                 -- profil.deal.projektname
    gesellschaft_id   integer,
    law_id            bigint NOT NULL REFERENCES screening.law(id) ON DELETE CASCADE,
    law_section_id    bigint NOT NULL REFERENCES screening.law_section(id) ON DELETE CASCADE,
    objekt_typ        text NOT NULL,
    objekt_id         integer NOT NULL,
    feature_text      text NOT NULL,                 -- snapshot of the embedded sentence
    score             numeric(5, 4) NOT NULL,
    norm_match        boolean NOT NULL DEFAULT false,
    form_unknown      boolean NOT NULL DEFAULT false,
    judge_relevant    boolean NOT NULL,
    judge_direction   text CHECK (judge_direction IN ('risiko', 'chance', 'neutral')),
    judge_reason      text NOT NULL,
    judge_confidence  real,
    model             text NOT NULL,                 -- LLM id of the judge
    trigger           text NOT NULL CHECK (trigger IN ('law', 'profil_update')),
    profile_version   integer,
    created_at        timestamptz NOT NULL DEFAULT now(),
    UNIQUE (company_id, law_section_id)
);
CREATE INDEX IF NOT EXISTS match_company ON screening.match (company_id, judge_relevant);
