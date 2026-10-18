<p align="center">
  <picture>
    <source media="(prefers-color-scheme: dark)" srcset="docs/klaris-logo-light.png">
    <img src="docs/klaris-logo-dark.png" alt="Klaris" width="220">
  </picture>
</p>

<p align="center">
  <b>From due diligence to proactive tax advice.</b><br>
  Klaris turns a DD data room into a living company profile, screens new tax law against it
  and tells the Steuerberater which clients to call, with the email already written.
</p>

<p align="center">
  <img alt="Python" src="https://img.shields.io/badge/Python-3.12-3776AB?logo=python&logoColor=white">
  <img alt="FastAPI" src="https://img.shields.io/badge/FastAPI-backend-009688?logo=fastapi&logoColor=white">
  <img alt="React" src="https://img.shields.io/badge/React-19-61DAFB?logo=react&logoColor=black">
  <img alt="PostgreSQL" src="https://img.shields.io/badge/Postgres-pgvector-4169E1?logo=postgresql&logoColor=white">
  <img alt="Docker" src="https://img.shields.io/badge/Docker-compose-2496ED?logo=docker&logoColor=white">
</p>

---

## The problem

After a due diligence, a tax advisor knows a company better than ever: its loss
carryforwards, shareholder changes, open tax years, real estate, intercompany loans. Then
the DD folder disappears into an archive. Months later a BFH ruling or a BMF letter changes
the rules for exactly that situation, and nobody connects the two.

## What Klaris does

1. **Upload the DD folder** (PDF and Excel). Klaris reads every document in parallel, with
   one AI agent per document, including scanned pages.
2. **Get a company profile** in which every value cites its source file and page.
   Conflicts between documents are kept side by side and shown to the advisor, never
   silently resolved.
3. **Confirm it.** The advisor stays in charge, and edits later are audited. Once the
   profile is confirmed, the raw DD documents are deleted; only the profile and its
   citations remain.
4. **Screen new law.** Klaris splits rulings and letters into sections and matches them
   against the profile in three stages: hard rules, vector similarity, then an LLM judge
   that has to explain *why* a client is affected.
5. **Reach out.** For each relevant change the advisor gets an alert and a ready email
   (one fixed template, no AI-written client mail), edits it and confirms the send. The
   calendar tracks replies and agreed Besprechungen.

> Human in the loop by design: the advisor confirms every profile and every email.

## Highlights

| | |
|---|---|
| **Every fact has a source** | Filename and page for each value; no value without a citation. |
| **Deterministic merge** | Plain code, no LLM, combines the agents' results. Agreement across documents means confirmed; disagreement means a conflict for the advisor. |
| **Explainable matching** | Rule filter, then max cosine similarity per feature, then an LLM judge with a written reason. Unchanged inputs reuse earlier verdicts at zero token cost. |
| **Privacy first** | Raw DD documents are deleted after confirmation; feature sentences contain no names or figures. |
| **Works in the background** | Uploads and screenings run as durable jobs, shown in the top bar with live status and pop-up results. |
| **Full audit trail** | Confirmations, edits, template changes, sends and client replies are logged. |

## How it works

```mermaid
flowchart TD
    %% Pitch note
    PITCH["Pitch point: contract clause about saving the company profile<br/>(optional annex, explicit consent, no raw DD data kept)"]:::note
 
    %% Phase 1: Due Diligence
    subgraph P1["1. Due Diligence: tax risks"]
        DD["Steuerberater finishes the DD"]
        UP["Upload folder to the platform<br/>PDF and Excel files"]
        DD --> UP
    end
 
    %% Phase 2: Document processing
    subgraph P2["2. Document processing"]
        ROUTE{"File type?"}
        PLUMB["pdfplumber<br/>text PDFs"]
        OCR["OCR model<br/>scanned PDFs"]
        XLS["Excel reader"]
        UP --> ROUTE
        ROUTE -->|text PDF| PLUMB
        ROUTE -->|scanned PDF| OCR
        ROUTE -->|Excel| XLS
 
        subgraph AGENTS["One sub agent per document, same prompt, run in parallel"]
            A1["Agent doc 1"]
            A2["Agent doc 2"]
            AN["Agent doc N"]
        end
        PLUMB --> AGENTS
        OCR --> AGENTS
        XLS --> AGENTS
    end
 
    %% Phase 3: Company profile
    subgraph P3["3. Company profile"]
        MERGE["Merge step: combine agent outputs<br/>(fixed JSON schema, source page per field, resolve conflicts)"]
        REVIEW["Steuerberater reviews and confirms the profile"]
        PROFILE[("Company profile<br/>structured fields + numeric values")]
        FEAT["Turn each feature into a descriptive sentence"]
        VEC[("Feature vectors<br/>one vector per feature")]
        DEL["Delete raw DD documents<br/>(folder reference kept until verified gone)"]
        UPD["Advisor updates entries later<br/>(audit log kept)"]
        A1 --> MERGE
        A2 --> MERGE
        AN --> MERGE
        MERGE --> REVIEW --> PROFILE
        PROFILE --> FEAT --> VEC
        VEC --> DEL
        PROFILE -.->|later edits| UPD
        UPD --> PROFILE
        UPD -.->|refresh changed vectors| FEAT
        MORE["Upload additional DD documents<br/>diff against the confirmed profile"]
        PROFILE -.-> MORE
        MORE -->|advisor confirms differences| UPD
        UI["Klaris HTTP adapter /api/ui<br/>German facts + citations; reviewed versions"]:::note
        UI -.- REVIEW
        JOBS["Background jobs: embed, clean up, refresh<br/>failed or interrupted: explicit retry or recover"]:::note
        FEAT -.- JOBS
    end
 
    %% Phase 4: Screening loop
    subgraph P4["4. Constant screening loop"]
        NEWS["Advisor starts screening<br/>ingest unseen server-side PDFs; skip stored content"]
        CHUNK["Split each law into sections<br/>and embed each section"]
        FILTER{"Stage 1: rule filter<br/>legal form and tax-type overlap"}
        SIM{"Stage 2: max similarity over<br/>company features above threshold?"}
        JUDGE{"Stage 3: LLM judge<br/>really relevant? why?<br/>(reused while its inputs are unchanged)"}
        NEWS --> CHUNK --> FILTER
        FILTER -->|passes| SIM
        SKIP["Not relevant: skip<br/>(an earlier decision becomes non-current,<br/>its open alert obsolete)"]
        FILTER -->|no| SKIP
        SIM -->|yes| JUDGE
        SIM -->|no| SKIP
        JUDGE -->|no| SKIP
        SKIP -.->|next law| NEWS
    end
    VEC ~~~ NEWS
    VEC --> SIM
    PROFILE --> FILTER
    UPD -.->|decisions wait for re-screening| FILTER
 
    %% Phase 5: Notify and offer
    subgraph P5["5. Notify and reach out"]
        ALERT["Alert the Steuerberater<br/>with matched feature and law section"]
        LABEL["Advisor labels company/law matches<br/>relevant, irrelevant or unlabeled"]
        DRAFT["Show fixed template with greeting/signature<br/>advisor edits exact final text"]
        CONF{"Confirm?"}
        SEND["Send one email for selected relevant laws<br/>current decisions, reserved snapshot + audit<br/>unclear delivery requires deliberate resolution"]
        ALERT --> LABEL --> DRAFT --> CONF
        CONF -->|yes| SEND
        CONF -->|edit| DRAFT
    end
    JUDGE -->|yes| ALERT
    SEND -.->|keep screening| NEWS
 
    PITCH -.-> P1
 
    classDef note fill:#fff4cc,stroke:#c9a400,color:#333
```

The same diagram lives in [`Project Diagram.mmd`](Project%20Diagram.mmd).

## Tech stack

| Layer | Technology |
|---|---|
| Frontend | React 19, Vite, TypeScript, Tailwind CSS 4, TanStack Query, lucide icons, sonner toasts |
| Backend | Python 3.12 with `uv`, FastAPI, Pydantic, asyncio |
| Documents | pdfplumber (text PDFs), GPT vision (scanned pages), openpyxl (Excel) |
| AI | OpenAI `gpt-6-luna` (extraction, law metadata, judge), `text-embedding-3-small` |
| Database | PostgreSQL with pgvector |
| Mail | Gmail SMTP with an app password, or local JSON files (`MAIL_MODE=log`) |
| Hosting | Docker Compose on one VM: Caddy (HTTPS and login gate), API, Postgres |

## Repository layout

```
backend/            FastAPI service, pipeline, matching, mail (see backend/README.md)
  app/              application code (api, agents, profile, matching, services, ui, db)
  db_scheme/        Postgres schema "profil" and vocabulary
  mock_laws/        source PDFs the screening reads (BFH I R 1/23, I R 37/22)
  tests/            pytest suite (fake LLM and fake mail, no network)
frontend/           React app (see frontend/README.md)
screening/contract/ JSON Schemas shared by frontend and backend
docs/               README assets
compose.prod.yml    production stack; Caddyfile is its entry point
Project Brief.md    design decisions and data model
```

## Run it locally

You need Python 3.12 with [uv](https://docs.astral.sh/uv/), Node 22, Docker and an OpenAI API key.

```bash
# 1. Database
cd backend
docker compose up -d

# 2. Backend (http://127.0.0.1:8000, API docs at /docs)
cp .env.example .env        # set OPENAI_API_KEY, REPOSITORY=postgres,
                            # DATABASE_URL=postgresql://dd:dd@localhost:5432/dd
uv sync
uv run python -m app.api

# 3. Frontend (http://127.0.0.1:5173)
cd ../frontend
cp .env.example .env.local  # VITE_DATA_SOURCE=http, VITE_API_URL=http://127.0.0.1:8000
npm ci
npm run dev
```

Without `VITE_DATA_SOURCE=http` the frontend runs on built-in sample data, with no backend
needed.

**Try it:** upload a DD folder under *Mandanten*, keep working while the top bar shows
progress, open the new client, then press *Screening starten* under *Gesetzesänderungen*.
Matches pop up at the top right. *Einstellungen → Alles zurücksetzen* starts over.

## Deploy on a VM

```bash
cp .env.production.example .env   # domain, login hash, DB password, OpenAI key, mail
docker compose -f compose.prod.yml up -d --build
```

Caddy obtains HTTPS certificates, puts a login in front of the whole app and proxies `/api`
to the backend. For real email set `MAIL_MODE=smtp` together with `GMAIL_ADDRESS` and
`GMAIL_APP_PASSWORD`. Keep `MAIL_REDIRECT_TO` set while testing so that every mail goes to
you, and make sure the VM can reach `smtp.gmail.com:587`.

## Tests

```bash
cd backend  && uv run pytest && uv run ruff check .
cd frontend && npm run typecheck && npm test && npm run build
```

The tests never call OpenAI or send mail. Postgres integration tests run when
`TEST_DATABASE_URL` is set; use a separate, disposable database for them. Tests that need the
mock data room skip unless its PDFs are in `backend/mock_files/` (they ship with the
submission, not with the repository).

## Status

Klaris is a hackathon prototype working on mock data: one Steuerberater, no user accounts,
and law sources limited to the PDFs in `backend/mock_laws`. Automatic fetching from BMF,
NeuRIS, Bundestag DIP and EUR-Lex, EU-only hosting with an AVV (data processing agreement)
and multi-user support are the next steps.
