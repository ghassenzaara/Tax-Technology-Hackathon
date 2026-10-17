# DD Monitor backend

- Install: `uv sync` (inside `backend/`)
- Configure: copy `.env.example` to `.env`; `OPENAI_API_KEY` is only needed for scanned PDFs
- Parse a folder: `uv run python -m app.cli <folder> [--json out.json] [--markdown]`
- Test: `uv run pytest`, lint: `uv run ruff check .`
- Without uv: `pip install -r requirements.txt` (exported with `uv export --no-hashes --no-dev -o requirements.txt`)
