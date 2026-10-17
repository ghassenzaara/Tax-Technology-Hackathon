# DD Monitor backend

- Install: `uv sync` (inside `backend/`)
- Configure: copy `.env.example` to `.env`; `OPENAI_API_KEY` is needed for scanned PDFs and for extraction
- Parse a folder: `uv run python -m app.cli <folder> [--json out.json] [--markdown]`
- Parse, extract and merge a profile draft: `uv run python -m app.cli <folder> --extract [--company-id PROJECT] [--schema FILE] [--json out.json]`
- Local Postgres: `docker compose up -d`, set `REPOSITORY=postgres` and `DATABASE_URL=postgresql://dd:dd@localhost:5432/dd`, then `uv run python -m app.cli --init-db [--seed]` (pipeline tables + teammate schema `profil` from `db_scheme/`; `--seed` loads the Nordhafen solution)
- Mock data room into the profile: `uv run python -m app.cli mock_files --extract --publish --company-id Hafenkante-extract` (the seeded solution is project `Hafenkante`, so compare against it)
- Merge stored candidates again: `uv run python -m app.cli --merge-only --company-id PROJECT --repository postgres [--publish]`
- Test: `uv run pytest`, lint: `uv run ruff check .`
- Postgres integration tests: set `TEST_DATABASE_URL=postgresql://dd:dd@localhost:5432/dd` (skipped otherwise)
- Without uv: `pip install -r requirements.txt` (exported with `uv export --no-hashes --no-dev -o requirements.txt`)
