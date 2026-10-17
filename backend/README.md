# DD Monitor backend

Steps 0 to 18 are implemented as backend services, CLI and HTTP API. Calendar is
deferred; there are no calendar tables, commands, routes, follow-up dates or reminders.

- Install: `uv sync` (inside `backend/`)
- Configure: copy `.env.example` to `.env`; `OPENAI_API_KEY` is needed for scanned PDFs, extraction and embeddings
- Parse a folder: `uv run python -m app.cli <folder> [--json out.json] [--markdown]`
- Parse, extract and merge a profile draft: `uv run python -m app.cli <folder> --extract [--company-id PROJECT] [--schema FILE] [--json out.json]`
- Local Postgres: `docker compose up -d`, set `REPOSITORY=postgres` and `DATABASE_URL=postgresql://dd:dd@localhost:5432/dd`, then `uv run python -m app.cli --init-db [--seed]` (pipeline tables + teammate schema `profil` from `db_scheme/`; `--seed` loads the Nordhafen solution)
- Mock data room into the profile: `uv run python -m app.cli mock_files --extract --publish --company-id Hafenkante-extract` (the seeded solution is project `Hafenkante`, so compare against it)
- Merge stored candidates again: `uv run python -m app.cli --merge-only --company-id PROJECT --repository postgres [--publish]`
- Test: `uv run pytest`, lint: `uv run ruff check .`
- Postgres integration tests: set `TEST_DATABASE_URL=postgresql://dd:dd@localhost:5432/dd` (skipped otherwise)
- Without uv: `pip install -r requirements.txt` (exported with `uv export --no-hashes --no-dev -o requirements.txt`)

## HTTP API (steps 0 to 18)

- Start: `uv run python -m app.api [--host 127.0.0.1] [--port 8000]` (defaults from `API_HOST` and `API_PORT`). Interactive docs at `http://127.0.0.1:8000/docs`.
- For the full flow use Postgres: `REPOSITORY=postgres` and `DATABASE_URL=postgresql://dd:dd@localhost:5432/dd` (after `docker compose up -d`). At startup the server creates the `pipeline` tables and schema `profil` if they are missing. With the default memory repository uploads, drafts and confirm work, but Profile and Update answer `not_supported`.
- Windows: psycopg's async mode needs a selector event loop, and uvicorn picks the proactor loop on Windows by default (the server then starts but fails on its first database call). `python -m app.api` always sets the selector loop. With the uvicorn command line use `uv run uvicorn app.api.app:create_app --factory --loop app.api.__main__:selector_loop_factory`.
- Contract for the UI team: `openapi.json` in this folder. Regenerate it after API changes: `uv run python -m app.api.openapi`.
- Flow: `POST /api/uploads` (multipart: `files` and `paths` once per file, optional `project_name`), poll `GET /api/companies/{id}/progress`, `GET .../draft`, `POST .../confirm` (embedding and cleanup then run in the background), `GET .../profile`, `POST .../profile/update`, `GET /api/audit`, `GET /api/companies`, `POST .../rerun`. Error codes and shapes: `UI.md` (main branch) and the OpenAPI file.
- Steps 11 to 18: `POST /api/laws` (multipart `file`, optional `replace`), `GET /api/laws[/{id}]`, `POST /api/screenings` (`{company_id?, law_id?, force, dry_run}`), `GET /api/companies/{id}/matches[?law_id&only_hits&include_history]`, `GET /api/alerts[?status&company_id&law_id]`, `GET /api/alerts/{id}`, `POST .../seen`, `POST .../dismiss`, `GET|POST /api/email-template`, `GET /api/alerts/{id}/email-preview`, `POST /api/alerts/{id}/send` (`{recipient, subject, body, confirmed: true, template_updated_at?}`), `GET /api/outreach`.
- Background jobs: law ingestion, screening, the post-confirm advance (embed, clean up) and the post-update refresh answer `202` with `Location: /api/jobs/{id}`; poll `GET /api/jobs/{id}` (`queued`, `running`, `succeeded`, `failed`, `interrupted`). One active job per company (`409 busy`). Nothing resumes after a restart: retry with `POST /api/jobs/{id}/retry`, or `POST /api/companies/{id}/recover`, which picks the work from the project state (Progress shows `recovery`). Sending an email is never a job; an uncertain delivery is resolved with the CLI (`--outreach-resolve`).
- Screening freshness: matches carry `is_current`, `input_fingerprint` and `invalidation_reason`; unchanged judge inputs reuse their verdict without an LLM call; at most `MATCH_MAX_CANDIDATES` changed sections are judged per request (the rest is reported as `deferred`; request screening again). A non-current alert cannot be previewed or sent. `SCREEN_AFTER_UPDATE=true` re-screens after each non-contact update (default false).
- Linked rows in the confirmed profile: `lists.finding[].values.normen` lists the finding's norm codes (sorted, `[]` when none). Other links are ids in the row values (for example `rechtsbehelf.veranlagung_id`, `pruefungsfeststellung.aussenpruefung_id`, `spa_absicherung.finding_id`); resolve them against the `id` of the rows in the corresponding list, and `quelle` against the cited document.
- Uploads are stored below `DATA_DIR/uploads/<company_id>` and deleted by step 10 (the run keeps the folder reference until the deletion is verified; a failed cleanup is recovered). Law uploads live in `DATA_DIR/law_uploads/<job_id>` until their ingestion succeeds. Limits (`MAX_FILE_MB`, `UPLOAD_MAX_TOTAL_MB`, `UPLOAD_MAX_FILES`), the run cap (`MAX_ACTIVE_RUNS`) and CORS origins (`CORS_ORIGINS`) are placeholders in `.env.example`.
- Without `OPENAI_API_KEY` the server starts, but extraction fails per document, embedding records an error and LLM work answers `503 llm_unavailable`.

## Confirm, embed, clean up (steps 8 to 10, Postgres only)

1. Review: `uv run python -m app.cli --review --company-id PROJECT --json review.json` prints the open decisions (conflicts, missing fields, conflicting list rows) and the draft version; `review.json` also holds a `decisions_template`.
2. Decide: fill in `action` for every entry of the template (`choose` with `group`, `value`, `leave_empty`; list rows `edit` with `cells`, or `drop`) and save it as `decisions.json`. Format: `ConfirmRequest` in `app/models/review.py`. Nothing is pre-selected.
3. Confirm: `uv run python -m app.cli --confirm decisions.json --confirmed-by "Name" --company-id PROJECT`. All or nothing; every row is stamped, decided `pruefhinweis` rows become `geklaert`. A confirmed project is never overwritten by a later `--publish`.
4. Embed and clean up: `uv run python -m app.cli --advance --company-id PROJECT [--delete-path data/PROJECT]` (or `--embed`, then `--cleanup`). Cleanup runs only when every vector exists, deletes the project's candidates, extraction records and drafts plus the given upload paths (must be inside `DATA_DIR`), and keeps `profil`, the vectors and the change history.

After confirmation the advisor can update entries: `uv run python -m app.cli --update changes.json --changed-by "Name" --company-id PROJECT` (format: `UpdateRequest` in `app/models/update.py`; edit, add, remove, end). The affected vectors are refreshed right away; if that fails the project is marked "embeddings stale" and `--advance` repairs it. `--profile` and `--audit` print the confirmed profile and its change history.

Known limits: files you write with `--json` or `--markdown` contain document text and are not deleted by step 10. Log lines carry file names and counts only. Backups, write-ahead logs and the OpenAI side are out of scope for now.

## Laws and matching (steps 11 to 15, Postgres CLI)

Set `DATABASE_URL` and use `--repository postgres`. `--init-db` applies `005_screening.sql`, creating `screening.law`, `screening.law_section` and `screening.match`, plus `006_law_change_summary.sql` for the source-level UI explanation. Ingestion and judging need `OPENAI_API_KEY`; dry-run scoring and stored-result reads do not.

```powershell
uv run python -m app.cli --ingest-law mock_laws/BFH-Urteil-I-R-1-23.pdf --repository postgres
uv run python -m app.cli --laws --repository postgres --json laws.json
# Use the law id printed by ingest; it differs between databases.
uv run python -m app.cli --screen --company-id Hafenkante-live --law-id LAW_ID --dry-run --repository postgres
uv run python -m app.cli --screen --company-id Hafenkante-live --law-id LAW_ID --repository postgres
uv run python -m app.cli --matches --company-id Hafenkante-live --only-hits --repository postgres --json hits.json
uv run python -m app.cli --screen-pending --company-id Hafenkante-live --repository postgres
```

Ingestion reuses the document parser, reads metadata with one structured model call (one repair retry), splits with plain code, and embeds sections with the same model as step 9. File hashes prevent duplicates; `--replace` atomically replaces the source and removes its old sections and matches. A failed ingest leaves stored data intact. `--screen-after-ingest` chains screening after a successful ingest.

Screening accepts `embedded` or `cleaned` projects with fresh vectors and takes the per-company busy lock. Stage 1 filters legal form and tax type; unknown values pass. Stage 2 uses the **maximum** feature cosine per section, then the threshold and candidate cap. Norm overlap breaks score ties but does not bypass the threshold. Stage 3 tries up to three features per section, stopping at the first yes; all no retains the highest-scoring pair. Output that remains invalid is unjudged and retried later. Stored results include both yes and no, one row per company/section. Identical reruns skip the judge; changing/removing the stored feature re-judges that section. `--force` re-judges every current candidate.

Omit `--company-id` to screen all eligible projects; omit `--law-id` to screen all stored sources. `--dry-run` stops before the judge and writes nothing. After a profile update, `--screen-pending` processes outstanding `profile_changed` audit entries; it marks them screened only when no candidate remains unjudged. `SCREEN_AFTER_UPDATE=true` enables this after the CLI's embedding refresh; default false. HTTP endpoints and scheduling remain deferred. Relevant stored matches now create alerts atomically (steps 16 to 18 below).

Tunables in `app/config.py`: `MATCH_SCORE_THRESHOLD` (0.54, initially calibrated on BFH I R 1/23 and Nordhafen), `MATCH_MAX_CANDIDATES` (20), `JUDGE_FEATURES_PER_SECTION` (3), `JUDGE_CONCURRENCY` (5), `JUDGE_REASONING_EFFORT`, `LAW_METADATA_PAGES` (3), and section token range 80/400/800. The cutoff retains all five observed hits with 17 candidates instead of 20; further labelled examples are needed to validate it. Models and dimensions must match; mismatches fail explicitly. Numeric and temporal rules are deferred.

The user-supplied BFH I R 1/23 ruling is handled as source kind `other`; there is no `profil.urteil` integration. The live positive-hit check passed: five stored hits against the existing loss/shareholder-change feature, with an unchanged rerun using zero judge tokens. Results and the initial threshold calibration are documented in [mock_laws/README.md](mock_laws/README.md). Tests: `uv run pytest tests/test_laws.py tests/test_screen.py`; Postgres end-to-end tests run when `TEST_DATABASE_URL` is set. All automated model calls use `FakeClient`.

For the planned Gesetzänderungen UI, `summary` supplies **Zusammenfassung**, `change_summary` supplies **Was ändert sich?**, and each positive match's `judge_reason` and `judge_direction` supply company-specific **Relevanz und Auswirkungen**. `change_summary` is generated in the existing metadata call: one or two German sentences about the change or clarification supported by the source pages. Rulings describe what the court clarifies; the model must not invent prior law, dates, or company effects. Missing evidence yields null. The migration leaves existing rows null; the supplied I R 1/23 source has been backfilled while preserving its vectors and five hits. Both `--ingest-law --json` and `--laws --json` expose the two source summaries. HTTP endpoints and frontend rendering are still outside this implementation.

## Alerts and email (steps 16 to 18, Postgres CLI)

Run `--init-db --repository postgres` to apply `007_contact_email.sql` and
`008_outreach.sql`. Existing positive matches get alerts without re-running the judge.
The singleton template is seeded only when absent, with `SENDER_NAME` and `SENDER_FIRM`;
later config changes do not overwrite it. Edit it explicitly to update the signature.

```powershell
uv run python -m app.cli --alerts --company-id PROJECT --repository postgres
uv run python -m app.cli --alerts --status all --law-id LAW_ID --repository postgres
uv run python -m app.cli --alert-show ALERT_ID --repository postgres
uv run python -m app.cli --alert-seen ALERT_ID --repository postgres
uv run python -m app.cli --alert-dismiss ALERT_ID --reason "Already discussed" --repository postgres
uv run python -m app.cli --template-show --repository postgres
# UTF-8 file: subject on the first line, plain-text body on remaining lines.
uv run python -m app.cli --template-edit template.txt --repository postgres
uv run python -m app.cli --email-preview ALERT_ID --repository postgres
# Explicit --yes is mandatory. Omitted subject/body files use the fixed template.
uv run python -m app.cli --email-send ALERT_ID --recipient client@example.test --body-file body.txt --yes --repository postgres
uv run python -m app.cli --outreach --company-id PROJECT --repository postgres --json outreach.json
# After manually checking Gmail Sent; never resend an uncertain pending delivery.
uv run python -m app.cli --outreach-resolve OUTREACH_ID --as sent --repository postgres
uv run python -m app.cli --outreach-resolve OUTREACH_ID --as failed --repository postgres
```

`--json FILE` works for these commands. Alert lists default to new/seen, with counts for
all statuses within company/law filters; new comes first, then score descending and
oldest first. Detail reads do not mark seen. Dismissed/actioned alerts stay closed;
re-screened new/seen negatives become obsolete, and obsolete positives become new again.
Source replacement obsoletes unhandled alerts while retaining source/delivery history.

Nullable `gesellschaft.ansprechpartner_email` holds the client's own contact address.
Extraction never guesses or substitutes a Kanzlei address. Nordhafen has no client address
in its PDFs, so preview reports a missing contact. Supply `--recipient` to send, or edit/
clear the field through the existing audited profile update. A typed send address does
not update the profile, embeddings or judge input. The schema/fixture handoff is recorded
in db_scheme/README; adding a client contact to a fixture/seed is still optional.

`MAIL_MODE=log` is the default: no network, JSON artifacts in `DATA_DIR/mail`, with a
successful outreach snapshot and audit. `MAIL_MODE=smtp` uses `smtp.gmail.com:587` with
STARTTLS, `GMAIL_ADDRESS` and `GMAIL_APP_PASSWORD`. See Google's
[SMTP settings](https://support.google.com/mail/answer/7104828) and
[app-password setup](https://support.google.com/accounts/answer/185833).
Set `MAIL_REDIRECT_TO` to your own inbox for prototype testing. Snapshots retain the
submitted recipient/subject/body and actual destination; `delivery_subject` stores
`[TEST an <original recipient>]` plus the submitted subject. No automatic SMTP retries.

The pending reservation commits before the provider call; a partial unique index blocks
parallel/repeated delivery. Success atomically marks sent/actioned and writes audit.
Definite rejection marks failed and permits retry. Crashes, cancellation or ambiguous
SMTP timeout/disconnect leave pending until manual resolution. The confirmed body is
stored independently of the template and transmitted as exact UTF-8 plain text. Provider
errors omit credentials/content. New audit operations stamp the internal advisor label
for the existing required column; no person input is accepted.

Tests: `uv run pytest tests/test_outreach.py`; set `TEST_DATABASE_URL` for transactional
Postgres checks. SMTP tests use a stub; other test sends use fake/log transports. A real
Gmail inbox/Sent-folder check awaits account setup. TODO: review wording/signature,
optional client contact fixture, client-end retention, OAuth, caps/timeout, and HTTP
endpoints (API plan section 11). Calendar, events and follow-up dates remain deferred.