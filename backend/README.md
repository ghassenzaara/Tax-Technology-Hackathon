# DD Monitor backend

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

## HTTP API (step 0)

- Start: `uv run python -m app.api [--host 127.0.0.1] [--port 8000]` (defaults from `API_HOST` and `API_PORT`). Interactive docs at `http://127.0.0.1:8000/docs`.
- For the full flow use Postgres: `REPOSITORY=postgres` and `DATABASE_URL=postgresql://dd:dd@localhost:5432/dd` (after `docker compose up -d`). At startup the server creates the `pipeline` tables and schema `profil` if they are missing. With the default memory repository uploads, drafts and confirm work, but Profile and Update answer `not_supported`.
- Windows: psycopg's async mode needs a selector event loop, and uvicorn picks the proactor loop on Windows by default (the server then starts but fails on its first database call). `python -m app.api` always sets the selector loop. With the uvicorn command line use `uv run uvicorn app.api.app:create_app --factory --loop app.api.__main__:selector_loop_factory`.
- Contract for the UI team: `openapi.json` in this folder. Regenerate it after API changes: `uv run python -m app.api.openapi`.
- Flow: `POST /api/uploads` (multipart: `files` and `paths` once per file, optional `project_name`), poll `GET /api/companies/{id}/progress`, `GET .../draft`, `POST .../confirm` (embedding and cleanup then run in the background), `GET .../profile`, `POST .../profile/update`, `GET /api/audit`, `GET /api/companies`, `POST .../rerun`. Error codes and shapes: `UI.md` (main branch) and the OpenAPI file.
- Uploads are stored below `DATA_DIR/uploads/<company_id>` and deleted by step 10. Limits (`MAX_FILE_MB`, `UPLOAD_MAX_TOTAL_MB`, `UPLOAD_MAX_FILES`), the run cap (`MAX_ACTIVE_RUNS`) and CORS origins (`CORS_ORIGINS`) are placeholders in `.env.example`.
- Without `OPENAI_API_KEY` the server starts, but extraction fails per document and embedding records an error.

## Confirm, embed, clean up (steps 8 to 10, Postgres only)

1. Review: `uv run python -m app.cli --review --company-id PROJECT --json review.json` prints the open decisions (conflicts, missing fields, conflicting list rows) and the draft version; `review.json` also holds a `decisions_template`.
2. Decide: fill in `action` for every entry of the template (`choose` with `group`, `value`, `leave_empty`; list rows `edit` with `cells`, or `drop`) and save it as `decisions.json`. Format: `ConfirmRequest` in `app/models/review.py`. Nothing is pre-selected.
3. Confirm: `uv run python -m app.cli --confirm decisions.json --confirmed-by "Name" --company-id PROJECT`. All or nothing; every row is stamped, decided `pruefhinweis` rows become `geklaert`. A confirmed project is never overwritten by a later `--publish`.
4. Embed and clean up: `uv run python -m app.cli --advance --company-id PROJECT [--delete-path data/PROJECT]` (or `--embed`, then `--cleanup`). Cleanup runs only when every vector exists, deletes the project's candidates, extraction records and drafts plus the given upload paths (must be inside `DATA_DIR`), and keeps `profil`, the vectors and the change history.

After confirmation the advisor can update entries: `uv run python -m app.cli --update changes.json --changed-by "Name" --company-id PROJECT` (format: `UpdateRequest` in `app/models/update.py`; edit, add, remove, end). The affected vectors are refreshed right away; if that fails the project is marked "embeddings stale" and `--advance` repairs it. `--profile` and `--history` print the confirmed profile and its change history.

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

Omit `--company-id` to screen all eligible projects; omit `--law-id` to screen all stored sources. `--dry-run` stops before the judge and writes nothing. After a profile update, `--screen-pending` processes outstanding `profile_changed` audit entries; it marks them screened only when no candidate remains unjudged. `SCREEN_AFTER_UPDATE=true` enables this after the CLI's embedding refresh; default false. HTTP endpoints, alerts and scheduling remain outside this milestone.

Tunables in `app/config.py`: `MATCH_SCORE_THRESHOLD` (0.54, initially calibrated on BFH I R 1/23 and Nordhafen), `MATCH_MAX_CANDIDATES` (20), `JUDGE_FEATURES_PER_SECTION` (3), `JUDGE_CONCURRENCY` (5), `JUDGE_REASONING_EFFORT`, `LAW_METADATA_PAGES` (3), and section token range 80/400/800. The cutoff retains all five observed hits with 17 candidates instead of 20; further labelled examples are needed to validate it. Models and dimensions must match; mismatches fail explicitly. Numeric and temporal rules are deferred.

The user-supplied BFH I R 1/23 ruling is handled as source kind `other`; there is no `profil.urteil` integration. The live positive-hit check passed: five stored hits against the existing loss/shareholder-change feature, with an unchanged rerun using zero judge tokens. Results and the initial threshold calibration are documented in [mock_laws/README.md](mock_laws/README.md). Tests: `uv run pytest tests/test_laws.py tests/test_screen.py`; Postgres end-to-end tests run when `TEST_DATABASE_URL` is set. All automated model calls use `FakeClient`.

For the planned Gesetzänderungen UI, `summary` supplies **Zusammenfassung**, `change_summary` supplies **Was ändert sich?**, and each positive match's `judge_reason` and `judge_direction` supply company-specific **Relevanz und Auswirkungen**. `change_summary` is generated in the existing metadata call: one or two German sentences about the change or clarification supported by the source pages. Rulings describe what the court clarifies; the model must not invent prior law, dates, or company effects. Missing evidence yields null. The migration leaves existing rows null; the supplied I R 1/23 source has been backfilled while preserving its vectors and five hits. Both `--ingest-law --json` and `--laws --json` expose the two source summaries. HTTP endpoints and frontend rendering are still outside this implementation.
