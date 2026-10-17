# Fix plan: resolve the nine UI integration findings

Status: implemented 2026-10-17 (see "Implementation status" at the end).

Source: `UI Backend Readiness Review.md`. Baseline: 365 passing tests, including local
Postgres integration, with clean Ruff lint and formatting checks.

## Summary and boundaries

Address all nine findings, then verify the complete steps 0–18 workflow through HTTP.
Read `CLAUDE.md`, the project brief, diagram and referenced implementation plans before
implementing. This task supersedes older instructions deferring matching/outreach HTTP
endpoints. Preserve existing uncommitted work and database records.

Confirmed decisions:

- Expose the full HTTP workflow, including legal-source uploads and screening controls.
- Run long operations as background jobs with polling.
- Require explicit UI retry after failures or server interruptions. Restarting must not
  automatically trigger LLM calls.
- Keep `SCREEN_AFTER_UPDATE=false` as the default; make it work consistently when enabled.
- Exclude calendar completely, including events, follow-up dates and reminders.
- Preserve explicit email confirmation and manual resolution of uncertain deliveries.
- Keep the existing mock-data, single-user scope and repository abstraction.
- Use fake/log providers in automated tests. Real Gmail acceptance requires account setup.

## 1. Correct persistence and recovery

### Finding 2: Durable cleanup

- Stop clearing upload-folder references when deleting working database rows.
- Resolve cleanup targets from stored run folders plus explicitly supplied CLI paths.
- Retain those references through filesystem deletion and verification. Clear references
  and mark the project `cleaned` together, only after verification succeeds.
- Treat already-deleted paths as successful cleanup so retries remain idempotent.
- For existing API projects whose folder reference was previously cleared, also check the
  deterministic `DATA_DIR/uploads/<company_id>` directory during recovery. Verify its
  resolved containment before deletion.
- Filesystem failures leave the project `embedded`, preserve deletion targets, and expose
  a recoverable error. A retry must verify actual files, not just an empty target list.

### Finding 8: Reliable draft publication

- Remove exception suppression around draft persistence.
- Apply the configured database-write retry policy; exhausted retries fail the run.
- Publish only after the draft has been saved successfully.
- Failed reruns must not expose an older draft as the latest successful result. Keep the
  existing latest-run review gate.
- Set `published=true` only after successful publication.

### Finding 6: Durable background work and explicit recovery

Add an idempotent migration after migration 008 for background jobs and matching freshness
metadata. Implement equivalent repository operations in memory and Postgres.

Persist jobs with:

- ID, operation, optional company ID, status, parameters, result, sanitized error,
  timestamps, and optional `retry_of`.
- Operations: law ingestion, screening, advance, and refresh.
- Statuses: `queued`, `running`, `succeeded`, `failed`, `interrupted`.
- Private source-file references where needed. Public responses exclude server paths,
  credentials, and vectors.

Persist each job before scheduling it. Serialize company work using the existing company
lock, and reject conflicting active operations with `409 busy`. Duplicate active
submissions must not launch duplicate work.

On shutdown or restart, mark unfinished jobs interrupted and expose recovery availability.
Do not resume LLM work automatically. Reconcile abandoned step markers under the existing
single-server deployment assumption; ordinary updates must also respect the configured
stale-lock timeout.

Recovery selects work from current state:

| Current state | Recovery action |
|---|---|
| Published | Refuse; use existing document rerun |
| Confirmed | Embed, then clean up |
| Embedded with fresh vectors | Retry cleanup |
| Embedded or cleaned with stale vectors | Fully rebuild vectors; clean up if still needed |
| Cleaned with fresh vectors | No-op unless configured screening remains pending |

A recovery request never repeats profile confirmation or sends an email. Recovery must
also be available for eligible projects without an existing job record, including work
interrupted before job creation. Retried jobs create a new attempt linked to the previous
one and revalidate current project state before executing.

## 2. Correct matching and update behavior

### Finding 3: Reconcile existing matches

- Add `is_current`, `input_fingerprint`, and `invalidation_reason` to stored matches.
- Preserve historical judge verdicts. Invalidation must not fabricate an LLM negative.
- Reconcile every previous match within the requested company/law scope, including
  sections excluded by rules, removed features, and scores below threshold.
- Mark obsolete decisions non-current and change only `new`/`seen` alerts to `obsolete`.
  Preserve dismissed/actioned history.
- Commit match changes and corresponding alert transitions atomically.
- Default match/hit reads exclude non-current decisions; provide an explicit history filter.
- Contact-only edits keep decisions current. Other profile edits invalidate company
  decisions until screening validates them again.
- Preview and send must refuse non-current matches or projects with stale vectors or
  active conflicting work. Enforce the send guard during the durable reservation, not
  solely during preview.
- Screening dry runs do not mutate matches, alerts, or audit screening markers.

### Finding 4: Complete cache invalidation

Calculate a deterministic fingerprint from:

- The judge's actual system prompt, output schema, model, and reasoning effort.
- The ordered candidate-feature IDs and complete judge messages.
- Feature ranking scores and the flags used for candidate selection.

Exclude the overall profile version from the fingerprint so unrelated contact changes do
not cause LLM calls.

Reuse a verdict only when its fingerprint matches current inputs. Reuse updates the
decision's current status and profile version without resetting advisor actions.

Keep the configured candidate cap as the maximum number of changed/uncached sections
judged per screening execution. Cached sections do not consume that budget. Report
remaining sections as deferred, not irrelevant; subsequent explicit screening requests
process the remaining work.

Unjudged or deferred sections remain non-current. Mark captured profile-change audit
entries screened only once reconciliation is complete. Expose incomplete screening and
partial results through reports/jobs so the UI can request another execution. Judge
failures remain visible and retryable; completed sections can be reused on retry.

Existing matches receive no guessed fingerprint during migration. Their open alerts
require one explicit screening pass before becoming current again. Migration/startup
must not invoke LLMs to perform that pass.

### Finding 5: Shared post-update workflow

- Use the same refresh-and-screen orchestration for CLI and HTTP updates.
- When `SCREEN_AFTER_UPDATE=true`, successful refresh is followed by pending screening.
- Failed refresh prevents screening. Failed screening preserves its error and outstanding
  audit markers.
- Expose the active job and pending-screening state through company progress.
- Keep contact-only updates free of vector refresh and automatic matching work.

## 3. Complete the HTTP contract and browser behavior

### Finding 1: Steps 11–18 endpoints

Keep routes thin and reuse the corrected services.

| Method and path | Behavior |
|---|---|
| `POST /api/laws` | Accept one PDF and optional `replace`; return an ingestion job |
| `GET /api/laws` | Return source metadata, summaries, and section counts |
| `GET /api/laws/{law_id}` | Return source metadata and sections without vectors |
| `POST /api/screenings` | Start screening; optional company/law filters, `force`, and `dry_run` |
| `GET /api/companies/{company_id}/matches` | Return matches; law, hits-only, and history filters |
| `GET /api/alerts` | Return filtered alerts and status counts |
| `GET /api/alerts/{alert_id}` | Return detail without marking seen |
| `POST /api/alerts/{alert_id}/seen` | Explicit seen transition |
| `POST /api/alerts/{alert_id}/dismiss` | Dismiss with an optional reason |
| `GET /api/email-template` | Read the singleton template |
| `POST /api/email-template` | Explicit, validated, audited template edit |
| `GET /api/alerts/{alert_id}/email-preview` | Return the existing preview contract |
| `POST /api/alerts/{alert_id}/send` | Send final recipient/subject/body with mandatory `confirmed=true` |
| `GET /api/outreach` | Filter history by company and alert |
| `GET /api/jobs/{job_id}` | Poll background-job state and result |
| `POST /api/jobs/{job_id}/retry` | Create a new attempt for failed/interrupted non-email work |
| `POST /api/companies/{company_id}/recover` | Start the state-selected recovery operation |

Long-task submissions return `202` with a job ID and polling location. Extend the existing
company progress response additively with active-job and recovery/pending-screening
information. Keep all existing endpoint behavior compatible.

Legal PDFs use generated storage names under `DATA_DIR/law_uploads/<job_id>`, the existing
file-size limit, and PDF validation. Retain inputs needed for failed/interrupted retries;
remove temporary inputs after successful ingestion. Duplicate-content protection remains
enforced. A dry-run HTTP job may persist job history but must not change screening data.

Email sending stays outside the generic job queue. Preserve the existing durable pending
reservation, exact snapshots, duplicate-send protection and audit trail. The send body
uses final recipient, subject and body, mandatory `confirmed=true`, and an optional preview
template timestamp. The path supplies the alert ID. Pending delivery resolution remains
a deliberate CLI action; no generic retry action can resend an email.

Retain the existing error envelope. Map validation to `422`, missing resources to `404`,
state/busy/duplicate/pending conflicts to `409`, definite provider failure to `502`, and
missing provider/LLM configuration to `503`. Include the outreach ID when a send attempt
exists; use typed errors rather than parsing exception messages. Keep errors sanitized.

### Finding 7: Consistent browser errors

Apply middleware in this effective order:

**request context → CORS → upload-limit guard → FastAPI error handlers/router**

Preserve the FastAPI object's lifespan and OpenAPI behavior. Early upload rejections and
unexpected errors must receive applicable CORS headers, `X-Request-ID`, and
`Cache-Control: no-store`. Never emit a second response after an error response has started.

### Finding 9: Complete confirmed-profile projection

Aggregate `finding_norm` associations into each finding's `values.normen`, returning a
sorted array of norm codes, including `[]` when none exist.

Reuse relationship-reading logic already used for profile snapshots. Preserve existing
profile fields and citations. Document how clients resolve other linked rows through
their exposed IDs.

## 4. Tests, rollout, and acceptance

Add regression coverage for every finding:

- Locked-file cleanup failure, crash between database/file operations, restart, and
  successful retry with actual files verified absent.
- Draft-write retry exhaustion, failed first publication, and failed rerun.
- Feature removal, empty feature sets, rule exclusion, below-threshold scores, law-scoped
  reconciliation, and unchanged dismissed/actioned history.
- Changed years/openness/norms, newly stronger features, deferred batches, judge failures,
  and zero LLM calls for unchanged fingerprints.
- HTTP updates with automatic screening enabled and disabled; contact-only updates.
- Interrupted jobs, abandoned locks, concurrent recovery requests, and explicit retry
  without automatic startup calls.
- Allowed-origin success, validation, oversized-upload, and unexpected-error responses.
- Review → confirmation → cleanup → profile reads retaining finding norms.
- Full Postgres HTTP flow: upload DD documents, confirm, poll cleanup, ingest a legal PDF,
  screen, inspect alerts, edit/preview the template, explicitly send through log mode,
  and inspect outreach/audit history.
- Duplicate sends, failed-send retry, uncertain pending delivery, and absence of any
  calendar behavior.

Apply migrations without resetting profiles, sources, audit, or outreach. Support
databases containing existing matches and interrupted work. Maintain memory/Postgres
behavior parity and preserve existing uncommitted changes.

Run the complete suite with local Postgres, Ruff lint, formatting checks, and OpenAPI
consistency checks. Regenerate `backend/openapi.json`; update the brief, diagram, CLAUDE
guidance, API plan, and backend usage documentation. Update this file with implementation
status, validation results and any remaining external setup.

Completion means all nine findings have passing regression tests and the complete
workflow is usable through HTTP without CLI intervention, except deliberate uncertain-email
resolution. Real Gmail delivery remains a separate acceptance check once the sender
account and own-inbox redirect are configured.

## Implementation status (2026-10-17)

All nine findings are implemented, each with regression tests; the steps 0 to 18 workflow
runs through HTTP without CLI intervention, except the deliberate resolution of an
uncertain email delivery (`--outreach-resolve`).

| Finding | Implementation | Tests |
|---|---|---|
| 1 HTTP contract | `api/routes/screening.py`, `outreach.py`, `jobs.py`; typed mail/LLM errors, `outreach_id` in the error body | `test_api_workflow.py` |
| 2 Durable cleanup | `delete_working_data` keeps folder references; `mark_cleaned` clears them with the state; targets include `DATA_DIR/uploads/<company_id>`; missing paths count as deleted | `test_jobs.py` (locked file, crash between rows and files, cleared references, retry) |
| 3 Reconciliation | `services/screen.py` + `apply_screening` (one transaction with alerts, coverage, audit markers); `is_current`, `input_fingerprint`, `invalidation_reason`; non-contact edits invalidate; send guard in preview and reservation | `test_reconcile.py` |
| 4 Fingerprints | `matching/judge.py::input_fingerprint`; the cap is the per-execution budget for changed sections (rest `deferred`); legacy rows `migrated` | `test_reconcile.py` |
| 5 Post-update workflow | `services/update.py::refresh_and_screen`, used by the CLI and the refresh job | `test_jobs.py`, Postgres flow in `test_api_workflow.py` |
| 6 Jobs and recovery | `runs/jobs.py`, `pipeline.jobs`; restart interrupts jobs and clears markers without LLM calls; explicit retry; state-selected recover; updates respect the stale-lock timeout and active jobs | `test_jobs.py`, `test_api_workflow.py` |
| 7 Browser errors | RequestContext -> CORS -> RequestGuard (body limits, JSON 500, no second response) | `test_api_workflow.py` |
| 8 Draft publication | draft save with `db_write_attempts`, no suppression; failure fails the run | `test_jobs.py`, `test_api_workflow.py` |
| 9 Profile projection | `finding.values.normen` from `profil.finding_norm` (sorted, `[]` when none) | `test_api_workflow.py` (Postgres) |

Migration `009_jobs_matching.sql` is additive and idempotent: it keeps profiles, sources,
audit, alerts and outreach; stored matches become non-current (`migrated`) until one
explicit screening pass, and no LLM is called at migration or startup.

Validation: 415 tests pass with local Postgres (`TEST_DATABASE_URL`), Ruff lint and format
checks pass, and `backend/openapi.json` is regenerated (a test compares it with the app).
Docs updated: `Project Brief.md`, `Project Diagram.mmd`, `CLAUDE.md`, `API Upload
Implementation Plan.md` (section 12), `backend/README.md`, `backend/.env.example`, and
`UI.md` plus brief and diagram in the main worktree (uncommitted).

Remaining external setup and notes:

- Real Gmail delivery: configure `MAIL_MODE=smtp`, `GMAIL_ADDRESS`, `GMAIL_APP_PASSWORD`
  and an own-inbox `MAIL_REDIRECT_TO`, then send one alert and check Inbox and Sent.
- Screening every stored law fails as a whole when one source has vectors of another model
  or dimension. The shared local test database holds such sources from older tests, so the
  Postgres HTTP acceptance test scopes its repository to its own source. Decide whether
  incompatible sources should be skipped and reported instead.
- `SCREEN_AFTER_UPDATE` stays `false` by default; with it, every non-contact update
  re-screens through the refresh job.

### Live end-to-end run (2026-10-17, after the fixes)

Before the run, every project except the seeded solution `Hafenkante` was deleted (with its
embeddings, matches, alerts and outreach), plus all legal sources; the audit log was kept.
Through HTTP against local Postgres with live OpenAI calls (`Nordhafen-E2E`, 134 s):
17/17 mock PDFs ok (79 s), 39 open decisions confirmed, the advance job embedded 15
features and cleaned up (upload folder gone), finding norms present in the profile; BFH
I R 1/23 ingested (32 sections, 6 s); screening: 21 candidates, 20 judged, 1 deferred
(budget 20), 3 hits; the rerun judged only the deferred section and reused 20; alerts,
preview, log-mode send (exact body), duplicate refused with `already_sent`, dismiss,
outreach and audit history all correct; a contact-only update started no job.

Found and fixed during the run:

- The new Postgres acceptance test changed the shared email template and did not restore
  it; the test now restores it, and the original text was restored (audited edit).
- A contact-only update left a profile-change marker pending; contact edits are now
  recorded as already screened, and a complete screening against every law also marks
  pending changes screened.
- Postgres tests left fake sources (2-dim vectors) behind, which make screening every law
  fail; the `pg_repository` fixture now removes the fake sources and test alerts it created.
- `backend/.env` has `REPOSITORY=memory`: start the server with `REPOSITORY=postgres` and
  `DATABASE_URL` for the full flow (the memory repository cannot embed the real schema:
  `AssessmentRow.vorbehalt_164` is None in memory; pre-existing, Postgres unaffected).
