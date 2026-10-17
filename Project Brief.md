# DD Regulatory Monitoring: Project Brief

Platform for Steuerberater. After a Due Diligence (DD), the advisor uploads the DD documents. The platform builds a structured **company profile**, monitors new and upcoming tax laws, and alerts the advisor when a law is relevant to a client, with a ready email (one fixed template, the same for every client). The goal: the advisor spots risks and tax planning opportunities (Gestaltungsmöglichkeiten) before anyone else and wins the client for follow-up work.

Process diagram: `Project Diagram.mmd`

---

## 1. End-to-end flow

Diagram: `Project Diagram.mmd`. Use these step numbers when referring to a part of the flow. Details for steps 1 to 7 are in section 5.2.

| # | Step | Phase (diagram) | Status |
|---|---|---|---|
| 0 | Upload the DD folder: HTTP API and upload layer (receive files, store per company, create the company id, start the run, progress and review endpoints) | 1. Upload | Built |
| 1 | Discover files in the uploaded DD folder | 2. Document processing | Built |
| 2 | Classify each file: text PDF, scanned PDF, Excel, unsupported | 2. Document processing | Built |
| 3 | Parse in parallel: pdfplumber (text PDF), GPT vision to markdown (scan), openpyxl (Excel) | 2. Document processing | Built |
| 4 | Normalize into the common document format | 2. Document processing | Built |
| 5 | One sub agent per document, same prompt and schema, run in parallel | 2. Document processing | Built |
| 6 | Per-document JSON, validated with Pydantic and written as candidate rows | 2. Document processing | Built |
| 7 | Merge into a profile draft plus conflicts | 3. Company profile | Built |
| 8 | Advisor reviews and confirms the profile; later the advisor can update entries (audit log kept, vectors refreshed) | 3. Company profile | Built (services, CLI and HTTP API) |
| 9 | Turn each feature into a descriptive sentence, embed it, store the vectors | 3. Company profile | Built |
| 10 | Delete raw documents, scan markdown, candidate rows, drafts | 3. Company profile | Built |
| 11 | Take a law (prototype: one hand-picked law PDF, parsed by our pipeline; no fetching) | 4. Screening loop | Planned |
| 12 | Split laws into sections and embed each section (deterministic splitter, norms by regex) | 4. Screening loop | Planned |
| 13 | Stage 1: rule filter on structured fields | 4. Screening loop | Planned |
| 14 | Stage 2: max vector similarity over company features | 4. Screening loop | Planned |
| 15 | Stage 3: LLM judge, store the match | 4. Screening loop | Planned |
| 16 | Alert the advisor with matched feature and law section | 5. Notify and reach out | Planned |
| 17 | Prepare the email from the fixed template (same text for every client, no LLM); the advisor may edit it before sending | 5. Notify and reach out | Planned |
| 18 | Advisor confirms, email is sent | 5. Notify and reach out | Planned |
| 19 | Add event to the in-app calendar | 5. Notify and reach out | Planned |

Step 0: the UI (teammates) sends the folder; our backend receives it through the HTTP API and upload layer (built following `API Upload Implementation Plan.md`; the contract for the UI team is `backend/openapi.json` plus `UI.md`). The API holds no business logic: it calls the services of the other steps. Calls: Health, Upload, Progress, Rerun, Draft, Confirm, Update, Audit, Companies, Profile. One upload request carries the whole data room with relative paths; untrusted paths are checked and files stream to a per-company folder (`data/uploads/<company_id>`); the run (steps 1 to 7 plus publish) runs in the background and the UI polls Progress. After Confirm, embedding and cleanup (steps 9 and 10) run in the background; after an Update the vectors are refreshed in the background. Contract clause about saving the profile is a pitch point only (no code).

**Ownership.** Decided: steps 11 and 12 are ours for the prototype and reduced to one hand-picked law PDF that was published earlier and fits the mock case; the backend parses, splits and embeds it itself, no fetching and no API call to a law source (the teammates' search for real sources is parked). Our team owns steps 0 to 15 and the backend logic of steps 16 to 19 (section 2.6). Matching is planned in `Matching Implementation Plan.md`. The frontend, the fixtures and the DB schema `profil` come from teammates. The frontend, the fixtures and the DB schema `profil` come from teammates.

---

## 2. Key design decisions

### 2.1 Document processing
- Route by file type: pdfplumber for text PDFs and tables, GPT 6-Luna vision for scans, pandas/openpyxl for Excel.
- Scans use **two LLM steps**: (1) GPT transcribes each scanned page to markdown, (2) the extraction agent runs on that markdown, same as for text PDFs.
- **One sub agent per document**, same prompt, run in parallel for speed.
- Each agent fills a **fixed JSON schema**, not free text. Every field carries a source (document + page).
- Agents write **candidate values** (append-only, one row per field per document) to the DB. Each agent output is validated with Pydantic against the DB schema before the write. Agents never write the final profile value.
- A **deterministic merge step** (plain code, no LLM call) reads the candidates and sets each field: same value from several documents = confirmed, different values = conflict with all candidates and sources kept for the advisor (for example two documents giving different revenue figures).
- Low-confidence fields and OCR pages are flagged for the advisor.

### 2.2 Company profile
- **The profile fields are predefined by a teammate** and delivered as a schema (JSON Schema / Pydantic model). The pipeline must load that schema as input and must not hardcode fields.
- The advisor confirms the profile once (protects against LLM hallucinations).
- **The advisor can update entries after confirmation** (facts change, mistakes are found): edit, add, remove or end an entry by hand. Every change is validated, stamped as entered by the advisor, recorded in the permanent audit log (when, old value, new value), and the affected feature vectors are refreshed. The audit log also records confirmations, email sends and template edits. The platform is single-user (one Steuerberater), so it records what and when, not who. There is no undo: the UI asks "are you sure" before saving, and a mistake is corrected by a new update. It does not rerun extraction and needs no documents. Updating a confirmed profile from the documents of a new DD is still open.
- After the profile is built, extracted and embedded, everything else is deleted: raw DD documents, scan markdown, and candidate rows. Only the profile and its feature vectors are kept (also keeps the DB small).
- **Sources after confirmation:** every profile value keeps a citation of filename and page (plus document class, title and date through `dokument`), so the advisor can check a doubtful value in the documents they still hold. The evidence quote and the full candidate list exist only while the draft is being reviewed (step 8); the publish step never writes the quote into `profil` (`fundstelle` holds filename and page only), and step 10 deletes the candidate rows and drafts where the quotes live. Conflict hints (`pruefhinweis`) keep the alternative values with file and page and the advisor's decision, never quotes. Values that are free text (for example a Merkmal's `kurztext`) can repeat the document's wording, because the value itself is the profile fact. No document viewer after confirmation.
- Security, AVV and scalability are out of scope for now. The prototype uses mock data only. Revisit later.

### 2.3 Company embeddings: a set of vectors per company
- **One vector per feature**, not one vector per company.
- Embed a **descriptive sentence**, not the raw value.
  Example: `WZ 56.10` becomes "Restaurant und Gastronomiebetrieb, Umsätze mit Speisen vor Ort und zum Mitnehmen".
- **Numbers stay out of embeddings** (employees, revenue, thresholds). They are plain fields checked by rules.
- Storage, one row per feature: `company_id, feature_type, text, vector`.

### 2.4 Law ingestion
- Prototype (decided): one law PDF supplied by the user, ingested by a CLI command; our pipeline parses it, splits it into sections and embeds each section (law text itself, not a paraphrase). Laws live in our schema `screening`, not in `profil`. Fetching from real sources is out of scope for now.
- Later sources (not built, start with 2 or 3, add more later): BMF-Schreiben, NeuRIS / rechtsinformationen.bund.de, Bundestag DIP, EUR-Lex / Cellar SPARQL, later BFH rulings and BGBl.
- Split each law into **sections** and embed each section separately.

### 2.5 Matching: 3 stages
1. **Rule filter** on structured fields: legal form, industry, region, numeric thresholds.
2. **Vector match:** `score(company, law_section) = max over features of cos(feature, section)`. Max, not average.
3. **LLM judge:** "relevant yes/no, why", referencing the matched feature and law section.

Every hit is stored as `(company_id, feature_id, law_section_id, score, judge_reason)`, which feeds the alert. The email does not use the hit details: it is the same template for every client.

### 2.6 Notify and reach out
- Alert shows: law section, matched feature, reason.
- The email is **one fixed template, the same for every client**. It is not drafted by an LLM and not tailored to the company or the law. Only the recipient (the client's contact) differs. The advisor sees the template text and **can edit it before sending**; an edit applies only to that one email, the stored template stays unchanged. The advisor then confirms and sends. **The send request carries the final recipient, subject and text from the UI; the backend sends exactly that** (not the stored template), validates it (non-empty, valid address) and stores it as a snapshot in `outreach`. The stored template is changed only through a separate explicit "edit template" action. The recipient can be changed per send as well.
- On confirm: send the email and create a calendar event in the app.
- **Audit:** every email send and every template edit writes an audit entry (when); the sent email itself is the `outreach` snapshot.
- Human in the loop is mandatory (the advisor stays responsible for the advice).
- **Split between backend and frontend (steps 16 to 19).** The frontend only displays and collects input. The backend owns the logic: step 16 stores each alert (match, law section, matched feature, reason, status new/seen/dismissed/actioned) and serves the alert list; step 17 stores the one email template, fills in the recipient and serves the preview text; step 18 accepts the advisor's confirmation with the optional edited text, sends the email through the mail provider (decided: Gmail with the user's own account, SMTP with an app password for the prototype) and records it in `outreach`; step 19 stores the calendar event and serves the calendar list. Details in `Alerts Email Calendar Implementation Plan.md`. The client contact's email address becomes a new profile field (`gesellschaft.ansprechpartner_email`, filled by extraction when a document states it, otherwise "not available" and the advisor types it at send time; never guessed). The template text is proposed in the plan and awaits the user's changes. A test redirect (`MAIL_REDIRECT_TO`) keeps mock sends from reaching third parties.

---

## 3. Data model

**Company profile (delivered):** Postgres schema `profil` in `backend/db_scheme/` (see its README): `deal`, `dokument` (with `dokument_klasse.rang` as source strength), `gesellschaft`, tri-state `merkmal_wert` (ja / nein / unbekannt), one table per profile block (`beteiligung`, `veranlagung`, `rechtsbehelf`, `steuerattribut`, `darlehen`, ...), `finding` (risk register), `pruefhinweis` (conflicts and open points), plus `urteil`, `embedding`, `alert` and views for the screening loop. Every fact carries provenance (`quelle_dok`, `fundstelle`, `stand`, `konfidenz`, `bestaetigt_von`, `bestaetigt_am`). The extraction pipeline keeps its working data in schema `pipeline` and publishes the merged draft into `profil` as unconfirmed rows.

**Later phases (first draft, to align with `profil`):**

```
companies(id, name, profile_json, contact_name, contact_email, created_at)
          -- profile_json follows the teammate's schema

company_features(id, company_id, feature_type, text, vector, source_doc, source_page)

law(id, kind, title, reference, published_at, effective_at, status, legal_forms, tax_types, summary, filename, content_hash)
    -- schema screening (ours), see Matching Implementation Plan.md

law_section(id, law_id, nr, section_ref, heading, page_from, page_to, text, norms, tax_types, model, vector)

match(id, company_id, law_id, law_section_id, objekt_typ, objekt_id, feature_text, score, norm_match,
      judge_relevant, judge_direction, judge_reason, trigger, profile_version, created_at)
      -- one stored pair per company and section; the alert status layer (new, seen, dismissed, actioned) comes with step 16

audit_log(id, kind, company_id, update_id, acted_at, table_name, field, row_key, change_kind, old_value, new_value, reason, profile_version, outreach_id, screened_at)
          -- permanent, append-only audit trail (database trigger rejects update and delete; not deleted by step 10). kind: profile_confirmed | profile_changed | email_sent | template_edited. company_id is empty for template edits; outreach_id references the sent-email snapshot; screened_at is set when matching has re-screened a profile change

alert(id, match_id, company_id, status, dismissed_reason, created_at, status_changed_at)
      -- schema screening; status: new | seen | dismissed | actioned | obsolete; one per relevant match

email_template(id, subject, body, updated_at)
          -- the one fixed template (single row); changed only by a separate, explicit "edit template" action, never by editing an email before sending

outreach(id, alert_id, recipient, delivered_to, subject, sent_body, status, error, provider_message_id, created_at, sent_at, template_updated_at)
         -- status: pending | sent | failed
          -- snapshot of what was really sent: recipient, subject and the exact final text the UI submitted. Stored on every send, so later template changes never alter the history

calendar_event(id, alert_id, outreach_id, company_id, title, due_date, notes, status, created_at, status_changed_at)
          -- follow-up event created with each successful send; status: open | done | cancelled
```

---

## 4. Suggested stack (to confirm in planning)

| Layer | Option |
|---|---|
| Backend | Python, FastAPI + uvicorn (HTTP API and upload layer, step 0) |
| Parsing | pdfplumber (text PDFs), GPT 6-Luna vision to markdown (scans), pandas/openpyxl (Excel) |
| Agents | OpenAI `gpt-6-luna` (1M context), parallel calls with asyncio, one per document, structured output validated with Pydantic |
| Package manager | uv |
| Database | Postgres via Supabase (free tier) or local Docker Postgres, always on. TODO: decide. Keep the DB layer behind one module so the switch is a config change |
| Vector DB | pgvector (Postgres) or Qdrant; Chroma for the demo |
| Embeddings | Multilingual model that handles German legal text well |
| Scheduler | Cron / APScheduler for the screening loop |
| Email + calendar | Gmail / Outlook API, in-app calendar table |
| Frontend | Dashboard: clients, profile view and update, audit log, alerts, email confirm and edit, calendar |
| Hosting | EU only, AVV with every provider (DD data is confidential) |

---

## 5. First build focus: document processing + parallel sub agents

**Start here.** Everything else (profile embeddings, screening, alerts) gets planned afterwards.

**Our scope:** parsing, LLM extraction logic and the backend in general. Frontend, test fixtures, the profile schema, and the other phases belong to teammates.

### 5.1 Goal
Input: a folder with PDF and Excel files.
Output: one JSON result per document (following the teammate's schema, with sources), produced by sub agents running at the same time, plus a merged profile draft.

### 5.2 Pipeline

```
folder
  -> discover files
  -> classify each file: text PDF | scanned PDF | Excel | unsupported
  -> parse (in parallel):
       text PDF    -> pdfplumber: text + tables, per page
       scanned PDF -> GPT vision page image -> markdown, per page
       Excel       -> pandas/openpyxl: one block per sheet
  -> normalized document: {doc_id, filename, type, pages: [{page_no, text, tables}]}
  -> spawn one sub agent per document (asyncio.gather, concurrency limit)
       same system prompt + schema, different document
       -> per-document JSON: field values + source page + confidence
  -> merge step -> profile draft + list of conflicts
```

### 5.3 Rules for the build
- **Start the agent as soon as its document is parsed**, do not wait for all documents (producer/consumer with an asyncio queue).
- **Concurrency limit** (semaphore, for example 5 to 10 agents at once) to stay under the LLM rate limits.
- **Retry with backoff** on rate limit and timeout errors. One failing document must not stop the others.
- **Long documents:** if a document exceeds the context window, split it into page chunks, run the agent per chunk and merge the chunk results with the same merge logic.
- **Text PDF vs scan detection:** if pdfplumber returns almost no text per page (threshold on characters per page), send the page to OCR. Handle this per page, since PDFs can be mixed.
- **Schema validation:** validate each agent output against the schema (Pydantic). Invalid output triggers one repair retry, then the document is flagged.
- **Every value carries** `source_doc`, `source_page`, `confidence`. No value without a source.
- **Merge step:** deterministic code, no LLM. Same value from several documents = confirmed. Numbers count as equal only when they differ by rounding; anything beyond rounding is a conflict; strings are compared after normalization. Different values = conflict, keep all candidates with their sources for the advisor to pick.
- **Chunking** happens inside the extractor, between parse and agent call: only when a document exceeds the context window. Chunk results go through the same merge logic.
- **Logging per document:** parse time, agent time, tokens, status. Needed for the pitch ("N documents in X seconds").
- **Schema as input:** load the teammate's schema from a file, so fields can change without code changes. Until it arrives, use a small placeholder schema with 3 or 4 fields.

### 5.4 Suggested module layout

```
app/
  ingest/
    discover.py      # list files, detect type
    pdf_text.py      # pdfplumber extraction
    pdf_ocr.py       # scanned pages: GPT vision -> markdown
    excel.py         # sheets to text blocks
    normalize.py     # common document format
  agents/
    prompt.py        # shared system prompt, built from the schema
    extractor.py     # one agent call per document (+ chunking, retry, validation)
    orchestrator.py  # queue, semaphore, parallel execution
  profile/
    schema.py        # loads the teammate's schema
    merge.py         # combine results, detect conflicts
  db/                # the single DB module: memory or Postgres (Supabase / Docker)
  llm.py             # shared OpenAI client and retry helper
  cli.py             # run the pipeline on a folder, print results and timings
tests/
  fixtures/          # provided later by a teammate: 1 text PDF, 1 scanned PDF, 1 mixed PDF, 1 Excel, 1 broken file
```

### 5.5 What to test
| Test | How |
|---|---|
| File classification | Fixture folder with each file type, assert the detected type |
| Text vs scan per page | Mixed PDF fixture: text pages go to pdfplumber, scanned pages to OCR |
| Excel parsing | Multi-sheet fixture, assert one block per sheet with correct content |
| Parallel execution | Mock the LLM with a 1 s delay, 10 documents must finish in about 2 s with limit 5 (not 10 s) |
| Agent starts early | Mock a slow parser for one file, assert other agents already ran |
| Failure isolation | One broken file + one LLM error: other documents still produce results, failures are flagged |
| Retry | Mock a rate limit error on the first call, assert success on retry |
| Schema validation | Mock invalid JSON output, assert repair retry and then a flag |
| Chunking | Long document fixture, assert several chunks and a correctly merged result |
| Merge | Two results with the same value = confirmed; different values = conflict with both sources |
| End to end | CLI on the fixture folder with a real LLM, check output by hand |

---

## 6. Open TODOs (build with placeholders, fill when available)

- Done: **DB schema** delivered as schema `profil` (`backend/db_scheme/`). Our working tables moved to schema `pipeline` (`field_candidates`, `document_extractions`, `profile_drafts`).
- Done: **Profile schema**: `backend/app/profile/profil_schema.json` mirrors `profil` (scalar fields for deal and target company, list fields for the row tables); a test keeps it in sync with the SQL. Every description defines period, unit and scope.
- TODO: **Marschland case** (second solution profile, several companies per deal) is still in progress with the teammates and was removed from `db_scheme/` (seed, checks, README). The delivered files had shuffled names and were renamed to match their content.
- TODO: **Several companies per deal**: the pipeline extracts one target company per run.
- Done: **Profile confirmation, embeddings, cleanup (steps 8 to 10)** and manual updates after confirmation, as backend services plus CLI (`--review`, `--confirm`, `--embed`, `--cleanup`, `--advance`, `--update`, `--profile`, `--audit`). Project state machine in `pipeline.project_state`; append-only audit log in `pipeline.audit_log` (trigger rejects UPDATE and DELETE except the matching step's `screened_at`). Live check on the Nordhafen mock data: 36 decisions, 14 features embedded in 10 s with no fallback sentence, cleanup verified, no evidence quote in any `fundstelle`.
- TODO: **Database choice**: Supabase free tier (pick an EU region) vs local Docker Postgres. Both work via `REPOSITORY=postgres` + `DATABASE_URL`; default is in-memory.
- Done: **OpenAI API key** via env var `OPENAI_API_KEY`; model id in `OPENAI_MODEL` (`gpt-6-luna`).
- Done: **json_schema via Chat Completions** works for `gpt-6-luna` with the full 44-field schema (live run on the 17 mock PDFs: about 80 s, 300k tokens, 16 of 25 checked facts identical to the seeded solution).
- TODO: **Rounding rule** confirmation. Applies only to monetary amounts (unit EUR); years, counts, percentages and keys compare exactly. Rule: granularity = last significant digit, trailing-zero granularity capped at 1000; equal when the more precise value rounds (half away from zero) to the coarser one. Confirm whether teammates need it for anything other than money.
- TODO: **List item identity**: rows keyed by free text (shareholder names, property objects) duplicate when documents spell them differently.
- TODO: **Chunk size and overlap** (placeholders: trigger 200,000, target 120,000 estimated tokens, 1 overlap page, 3.5 chars per token).
- TODO: **Reasoning effort, confidence mapping and caps**, and the konfidenz thresholds (hoch from 0.8, mittel from 0.5) (placeholders in `config.py`).
- TODO: **String aliases** for legal forms (GmbH vs G.m.b.H.).
- Done: **company_id creation** by the upload layer: slug of the optional project name from the UI (or generated from the date and a random part), unique against stored projects, deals in `profil` and seeded project names, never reused. The CLI still defaults to the folder name.
- TODO: **Scan detection threshold** (characters per page).
- TODO: **Concurrency limit** values (parse, OCR, agents; default 5 each).
- Done: **Fixtures**: 17 mock data-room PDFs (Nordhafen) in `backend/mock_files/`; `10_seed_nordhafen.sql` is the expected profile.
- TODO: **Email template**: the one fixed text sent to every client (author, language, subject). The advisor may edit the text per send (decided); TODO: whether edits can be saved back into the template.
- Done: `fundstelle` holds filename and page only (no evidence quote).
- TODO: **Free-text cells in list rows** (Merkmal `kurztext`, `sitz_wohnort`, `anteile`) make rows conflict when documents word them differently; on the mock data most of the 36 review decisions were such wording differences. Decide whether free-text cells should stop counting as a conflict (keep the strongest source's wording) or stay advisor decisions.
- TODO (steps 8 to 10, details in `Confirm Embed Cleanup Implementation Plan.md`): whether needs-review values need explicit acknowledgement; let the advisor complete skipped list rows and add new rows; how a later DD updates a confirmed profile from documents (manual updates are decided); which tables support ending an entry, whether a reason is mandatory, history inside `profil`; embed `unbekannt` Merkmale (default no); industry (WZ) embedding needs an allowed type in `embedding`; embedding model id, dimension, batch size; statute to tax-type mapping and the `offen` derivation; switch the vector column to `vector(1536)` with HNSW; per-company upload folder for cleanup.
- TODO (step 0, built; details in `API Upload Implementation Plan.md`): chunked or resumable uploads, adding files to an existing company, cleanup of abandoned uploads; tune the upload limits (placeholders: 50 MB per file, 500 MB total, 300 files) and the global run cap (2); the UI team's dev URL and port for CORS (defaults: localhost 3000 and 5173); whether the UI needs a live per-document "processing" state; server hosting. Live check through HTTP on the Nordhafen mock data: run 82 s for 17 PDFs, 38 decisions, embedding and cleanup in the background in 9 s.
- TODO (steps 11 to 15, details in `Matching Implementation Plan.md`): the law PDF (user supplies it) and its heading patterns; score threshold and candidate cap (calibrate with `--screen --dry-run`); judge prompt, reasoning effort, features per section; temporal rule (law effective date against open years) and numeric condition rules; whether a norm match bypasses the threshold; real law sources and court rulings (`profil.urteil`) later; `vector(1536)` with HNSW; HTTP endpoints for laws and matches.
- TODO (steps 16 to 19, details in `Alerts Email Calendar Implementation Plan.md`): review and change the proposed template text, signature, sender name and firm; add the column `ansprechpartner_email` to the teammates' `profil.gesellschaft` (tell them) and a contact email to one fixture; Gmail app password and `MAIL_REDIRECT_TO` for the live check (Gmail API with OAuth later); whether a dismissed alert can be reopened; calendar time of day, reminders and whether `v_fristen` deadlines show in the calendar; follow-up default (7 days); HTTP endpoints for alerts, template, send and calendar.
- Decided: alerts live in our schema `screening` (the teammates' `profil.alert` refers to rulings and has a person column and stays unused).
- Done: **Single user**: the platform serves one Steuerberater. No login, no users, no person in any call; the audit log records what and when only. Existing code still carries person fields until the change request in `Confirm Embed Cleanup Implementation Plan.md` (section 10) is applied.
