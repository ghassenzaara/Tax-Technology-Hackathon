-- Existing databases, including pipeline-only startup before profil is first created.
DO $$ BEGIN
    IF to_regclass('profil.gesellschaft') IS NOT NULL THEN
        ALTER TABLE profil.gesellschaft ADD COLUMN IF NOT EXISTS ansprechpartner_email text;
    END IF;
END $$;
