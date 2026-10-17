-- Source-level explanation for the UI's "Was ändert sich?" section. Existing sources
-- remain null until metadata is refreshed; no matches or section vectors are changed.
ALTER TABLE screening.law ADD COLUMN IF NOT EXISTS change_summary text;
