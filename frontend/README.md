# Klaris frontend

```powershell
npm.cmd ci
Copy-Item .env.example .env.local
npm.cmd run dev
```

The example selects `VITE_DATA_SOURCE=http` and `VITE_API_URL=http://127.0.0.1:8000`.
Start the backend separately from `backend/` with `uv run python -m app.api`.
Restart Vite after environment changes. Without HTTP configuration, the mock adapter runs.

Use `MAIL_MODE=log` in backend settings for local verification. The screening button reads
the server's `LAW_SOURCE_DIR`, defaulting to `backend/mock_laws`; no browser law upload is
required. Failed jobs offer explicit retry, and client pages show processing/recovery.
Email sends always go through the confirmation dialog; uncertain delivery requires CLI
resolution before another send.

Checks: `npm.cmd run typecheck`, `npm.cmd test`, `npm.cmd run build`.
The HTTP adapter tests use fake fetch; backend UI tests validate real response shapes
against `screening/contract` using fake models and fake/log mail.
