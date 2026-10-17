# DD Regulatory Monitoring: Project Brief

Platform for Steuerberater. After a Due Diligence (DD), the advisor uploads the DD documents. The platform builds a structured **company profile**, monitors new and upcoming tax laws, and alerts the advisor when a law is relevant to a client, with a ready email draft. The goal: the advisor spots risks and tax planning opportunities (Gestaltungsmöglichkeiten) before anyone else and wins the client for follow-up work.

Process diagram: `Project Diagram.mmd`

---

## 1. End-to-end flow

Diagram: `Project Diagram.mmd`. Use these step numbers when referring to a part of the flow. Details for steps 1 to 7 are in section 5.2.

| # | Step | Phase (diagram) | Status |
|---|---|---|---|
| 1 | Discover files in the uploaded DD folder | 2. Document processing | Built |
| 2 | Classify each file: text PDF, scanned PDF, Excel, unsupported | 2. Document processing | Built |
| 3 | Parse in parallel: pdfplumber (text PDF), GPT vision to markdown (scan), openpyxl (Excel) | 2. Document processing | Built |
| 4 | Normalize into the common document format | 2. Document processing | Built |
| 5 | One sub agent per document, same prompt and schema, run in parallel | 2. Document processing | Planned |
| 6 | Per-document JSON, validated with Pydantic and written as candidate rows | 2. Document processing | Planned |
| 7 | Merge into a profile draft plus conflicts | 3. Company profile | Planned |
| 8 | Advisor reviews and confirms the profile | 3. Company profile | Not started |
| 9 | Turn each feature into a descriptive sentence, embed it, store the vectors | 3. Company profile | Not started |
| 10 | Delete raw documents, scan markdown and candidate rows | 3. Company profile | Not started |
| 11 | Fetch new and upcoming laws | 4. Screening loop | Not started |
| 12 | Split laws into sections and embed each section | 4. Screening loop | Not started |
| 13 | Stage 1: rule filter on structured fields | 4. Screening loop | Not started |
| 14 | Stage 2: max vector similarity over company features | 4. Screening loop | Not started |
| 15 | Stage 3: LLM judge, store the match | 4. Screening loop | Not started |
| 16 | Alert the advisor with matched feature and law section | 5. Notify and reach out | Not started |
| 17 | Draft the email | 5. Notify and reach out | Not started |
| 18 | Advisor confirms, email is sent | 5. Notify and reach out | Not started |
| 19 | Add event to the in-app calendar | 5. Notify and reach out | Not started |

Before step 1: the advisor uploads the DD folder in the UI (teammate). Contract clause about saving the profile is a pitch point only (no code).

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
- After the profile is built, extracted and embedded, everything else is deleted: raw DD documents, scan markdown, and candidate rows. Only the profile and its feature vectors are kept (also keeps the DB small).
- Security, AVV and scalability are out of scope for now. The prototype uses mock data only. Revisit later.

### 2.3 Company embeddings: a set of vectors per company
- **One vector per feature**, not one vector per company.
- Embed a **descriptive sentence**, not the raw value.
  Example: `WZ 56.10` becomes "Restaurant und Gastronomiebetrieb, Umsätze mit Speisen vor Ort und zum Mitnehmen".
- **Numbers stay out of embeddings** (employees, revenue, thresholds). They are plain fields checked by rules.
- Storage, one row per feature: `company_id, feature_type, text, vector`.

### 2.4 Law ingestion
- Sources (start with 2 or 3, add more later): BMF-Schreiben, NeuRIS / rechtsinformationen.bund.de, Bundestag DIP, EUR-Lex / Cellar SPARQL, later BFH rulings and BGBl.
- Split each law into **sections** and embed each section separately.

### 2.5 Matching: 3 stages
1. **Rule filter** on structured fields: legal form, industry, region, numeric thresholds.
2. **Vector match:** `score(company, law_section) = max over features of cos(feature, section)`. Max, not average.
3. **LLM judge:** "relevant yes/no, why", referencing the matched feature and law section.

Every hit is stored as `(company_id, feature_id, law_section_id, score, judge_reason)`, which feeds the alert and the email.

### 2.6 Notify and reach out
- Alert shows: law section, matched feature, reason.
- LLM drafts the email. The advisor edits or confirms in one click.
- On confirm: send the email and create a calendar event in the app.
- Human in the loop is mandatory (the advisor stays responsible for the advice).

---

## 3. Data model (first draft)

```
companies(id, name, profile_json, contact_name, contact_email, created_at)
          -- profile_json follows the teammate's schema

company_features(id, company_id, feature_type, text, vector, source_doc, source_page)

laws(id, source, title, url, published_at, effective_at)

law_sections(id, law_id, section_ref, text, vector)

matches(id, company_id, feature_id, law_section_id, score,
        judge_relevant, judge_reason, status, created_at)
        -- status: new | drafted | sent | dismissed

outreach(id, match_id, email_subject, email_body, sent_at, calendar_event_id)
```

---

## 4. Suggested stack (to confirm in planning)

| Layer | Option |
|---|---|
| Backend | Python, FastAPI |
| Parsing | pdfplumber (text PDFs), GPT 6-Luna vision to markdown (scans), pandas/openpyxl (Excel) |
| Agents | OpenAI `gpt-6-luna` (1M context), parallel calls with asyncio, one per document, structured output validated with Pydantic |
| Package manager | uv |
| Database | Postgres via Supabase (free tier) or local Docker Postgres, always on. TODO: decide. Keep the DB layer behind one module so the switch is a config change |
| Vector DB | pgvector (Postgres) or Qdrant; Chroma for the demo |
| Embeddings | Multilingual model that handles German legal text well |
| Scheduler | Cron / APScheduler for the screening loop |
| Email + calendar | Gmail / Outlook API, in-app calendar table |
| Frontend | Dashboard: clients, alerts, draft editor, calendar |
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

- TODO: **DB schema** (from teammate). Pydantic models must match it. Placeholder until then: `field_candidates(id, company_id, field_name, value, source_doc, source_page, confidence, created_at)`.
- TODO: **Profile schema** (from teammate). Load from file; 3 or 4 field placeholder until then.
- TODO: **Database choice**: Supabase free tier (pick an EU region) vs local Docker Postgres.
- TODO: **OpenAI API key** via env var (model id is `gpt-6-luna`; keep it in one config constant).
- TODO: **Rounding rule** for the merge step: define "differs by rounding only" (for example same value at the coarser of the two precisions).
- TODO: **Chunk size and overlap** for long documents.
- TODO: **Scan detection threshold** (characters per page).
- TODO: **Concurrency limit** value (5 to 10).
- TODO: **Fixtures** (teammate).
