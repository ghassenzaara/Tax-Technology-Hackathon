# UI integration guide

For the UI team. This lists the features the UI must have and the constraints it must follow so it integrates smoothly with the backend. Exact data shapes (including the profile draft) come from the API: `backend/openapi.json` (on the `backend` branch) is the generated OpenAPI contract you can build mocks and types from; a running server also serves it at `/openapi.json` and interactive docs at `/docs`. Section 3 lists the calls.

**Backend status:**
- Built, with HTTP calls for everything (steps 0 to 18): upload, parsing, one extraction agent per document, the merge into a profile draft (steps 1 to 7), advisor confirmation, updating a confirmed profile with the audit log, embeddings and cleanup (steps 8 to 10), legal-source upload (one PDF, no fetching) and the three-stage matching (steps 11 to 15), alerts, the one fixed email template with preview and edit, and the confirmed send (steps 16 to 18; log mode by default, Gmail once configured). Every feature in sections 1 to 3 can be built against the real API now.
- Not part of the platform: the calendar (step 19), follow-up dates and reminders (deferred by decision).
- Anything below marked *proposed* is final only after the backend team confirms it.
- No authentication in the prototype. Use mock data only, no real client data.

Process diagram: `Project Diagram.mmd`. Step numbers: `Project Brief.md` section 1.

## 1. Features the UI must have

1. **Upload:** pick the DD folder and send all files, keeping each file's relative path.
2. **Progress:** while the backend works, show one row per document with its status and warnings. Failed, skipped and unsupported files stay visible after the run.
3. **Profile review (step 8):** the advisor checks the company profile before it is saved:
   - see every profile field with its value, or its candidate values when documents disagree;
   - see the source of every value (filename, page, evidence quote) while reviewing;
   - resolve conflicts by picking a candidate or typing their own value;
   - fill in or deliberately leave empty any field where nothing was found;
   - see which values need review;
   - confirm the profile with one action.
4. **Client list and confirmed profile:** list the companies with their state, and show a confirmed profile with each value's citation (filename and page; for values the advisor typed: "entered by the advisor" and the date).
5. **Update entries (after confirmation):** the advisor can change a confirmed profile at any time, without a new review:
   - edit a value, edit cells of a list row, add a new row, remove a row that was a mistake, or end an entry that is no longer valid (an end date instead of deleting, so the history stays);
   - optionally give a reason for the change;
   - go through the flow Update entries, type the new values, review a summary, an extra "are you sure" step, then save (there is no undo; a mistake is corrected by a new update);
   - see the audit log: what changed and when (field, old value, new value, reason), plus confirmations, sent emails and template edits;
   - see a hint "embeddings stale" while the backend refreshes the search data after a change.
6. **Legal sources and screening (steps 11 to 15):** upload a legal-source PDF, list the sources (with "Zusammenfassung" = `summary` and "Was ändert sich?" = `change_summary`), start screening for one or every company, and show a company's matches (law section, matched feature, direction, reason = "Relevanz und Auswirkungen"). Long work is a background job: poll it and offer Retry when it failed or was interrupted.
7. **Alerts and email (steps 16 to 18):** an alert list with status (new, seen, dismissed, actioned, obsolete) and counts, an alert detail (law section, matched feature, reason) that does not mark the alert seen by itself, explicit "seen" and "dismiss" actions, an email screen and a template-edit screen, and a sent-mail history. The email starts from **one fixed template, the same for every client**, not drafted per company or law. The screen shows the template text; the advisor **can edit text, subject and recipient before sending**, then confirms with an "are you sure" step. The recipient is pre-filled from the profile field *client contact email*; when the profile has none, the preview says "Keine E-Mail-Adresse im Profil gefunden" and the advisor types one. The backend sends exactly what the UI submits and stores every sent email (edited or not) as a snapshot; sends and template edits also appear in the audit log. Editing one email never changes the template; the template changes only through the separate "edit template" screen.
8. **Recovery:** when Progress reports `recovery.available`, offer one "Finish processing" action (Recover). It never repeats the confirmation and never sends an email.

## 2. Constraints the UI must follow

**Fields and values**
1. **Never hardcode profile fields.** The field list comes from a schema delivered by another team and will change. Render the review screen from whatever fields the backend sends.
2. **Respect field types.** Types are string, integer, number, boolean, date and enum. Numbers are plain JSON numbers: format them for display only (German style, for example 1.234,50) and never parse a formatted string back. Dates are ISO strings (2023-12-31). Enum inputs must offer only the allowed values.
3. **Values are validated by the backend before saving.** Show its error messages next to the fields; do not assume your own validation is enough.

**Sources and review**
4. **Always show the source of a value.** While reviewing (step 8): filename, page and evidence quote. After the profile is confirmed: filename and page only, because the evidence quote is removed (step 10) and the documents are deleted. The advisor uses filename and page to re-check a doubtful value in their own copy of the documents. No value is displayed without a source. For an Excel file the page is the sheet's position in the workbook.
5. **Show review flags.** Values from scanned pages (OCR) or with unverified evidence are marked needs-review. Make that visible.
6. **Evidence quotes are untrusted text from documents.** Render them as plain text, never as HTML or markdown.
7. **No "open original document".** Raw documents are deleted after processing. During review show the quote and page number; on a confirmed profile show filename and page only. A confirmed profile has no conflict alternatives either: only the chosen value and its source.

**Conflicts and confirmation**
8. **Never pre-select a value for a conflict.** If the backend sends a suggestion, it is a hint only; the advisor has to choose.
9. **Human in the loop.** Nothing becomes the confirmed profile until the advisor confirms. Confirm stays disabled while any field is neither resolved nor explicitly left empty.
10. **Keep all candidates visible** for a conflict, with their sources, so the advisor can compare. Values that differ only by rounding are grouped by the backend; show them as one value with an indication that alternatives exist.

**Documents and statuses**
11. **Show every status and warning.** One failed file never means a failed run, and the advisor must know what was not read.
12. **Status vocabulary** (use these exact meanings):
    - Parsing: pending (not parsed yet), ok, partial (usable, with warnings), failed, unsupported.
    - Extraction: pending, ok, partial, failed, skipped, write_failed.
    - Profile field: confirmed (two or more documents agree), single_source (one document), conflict (different values), missing (nothing found; stored as `unbekannt` in the database).
    - Run: queued (waiting for a free slot), running, finished (every document has a final status; single failures included), failed (the run itself crashed or the profile could not be written: see `last_error`), interrupted (server stopped; offer Rerun).
13. **Unknown values must not crash the UI.** If the backend sends a status or type you do not know, show it as plain text.

**Technical**
14. **IDs are opaque strings.** Do not parse or build them. `company_id` identifies one client company and tags all its data so clients never mix. In the database it is the deal's project name (`profil.deal.projektname`; the CLI defaults it to the folder name). The backend creates it at upload (from the optional project name the UI sends, made unique) and returns it; the UI sends it on every later call.
15. **Upload limits:** supported files are PDF, xlsx and xlsm. Everything else is accepted but reported as unsupported. Size limit per file is 50 MB (placeholder). Do not block the upload on file type; let the backend report it.
16. **Progress by polling** every 1 to 2 seconds while a run is active (`run.state` `queued` or `running`), and after Confirm or Update while `active_job` is set (until the state is `cleaned`, or `latest_job.status` is `failed` or `interrupted`). Calls that start long work (law upload, screening, retry, recover) answer `202` with `Location: /api/jobs/{id}`: poll that job until `succeeded`, `failed` or `interrupted`. Nothing resumes after a server restart: an `interrupted` or `failed` job shows its `error` and a Retry button (`POST /api/jobs/{id}/retry`). No websockets.
17. **Errors** arrive as an object with a machine-readable code and a human-readable message. Show the message.
18. **CORS:** tell the backend team your dev URL and port so the backend allows it (allowed now: `http://localhost:3000`, `http://localhost:5173` and the same on `127.0.0.1`).
19. **Language:** documents are German. Quotes and extracted text values are shown exactly as in the document. The UI language is your decision.

**Confirmed profile, updates and email**
20. **Profile version.** The confirmed profile has a version number (starts at 1, +1 per update). Send the version you saw with every update; if the backend answers "stale profile", reload and let the advisor redo the change.
21. **Update rules.** Required fields cannot be emptied. Values typed by the advisor are validated like any other (type, enum values, length, date format); show the per-field messages. An update is refused before the profile is confirmed, while the company is busy (a step or background job is running), or when the version is stale; the error codes tell these apart.
22. **Advisor-entered values.** After an update the citation of a changed value is "entered by the advisor" with the date, not a filename. Do not invent a document source for it.
23. **Project state vocabulary:** published (draft saved, waiting for review), confirmed, embedded, cleaned; plus the flag "embeddings stale". Show them as plain status text.
24. **Confirm before saving an update.** Show a summary of the changes and an explicit "are you sure" step before calling Update. There is no undo; the advisor corrects a mistake with a new update.
25. **Email.** Send the final recipient, subject and text from the screen with `confirmed: true`; the backend sends exactly that and never the stored template. Show the template text as the starting point only. Never write edits back to the template from the email screen. Sending is never retried automatically: `send_failed` allows a new send by the advisor; `send_pending` means an earlier delivery is unclear and must be checked in Gmail "Gesendet" and resolved by the backend team before anything else is sent for that alert.
26. **Only current matches.** A match or alert whose `match.is_current` is false waits for screening (for example after a profile change) and cannot be previewed or sent; show its old reason as history only and offer "Screen again". A screening report with `deferred` or `unjudged` sections is incomplete: offer another screening.

27. **Single user.** The platform serves one Steuerberater: there is no login and no person field in any call. You can still get "stale" and "busy" errors (two browser tabs, a double click, a background step still running); reload when that happens.

## 3. Calls

All paths start with `/api`. Ids are opaque strings (URL-encode them in paths). Bodies and responses are JSON except Upload. No call takes a person: the backend stamps "Steuerberater" itself. Responses are never cached (`Cache-Control: no-store`); every response carries an `X-Request-ID` header.

| Call | Method and path | Body | Answer |
|---|---|---|---|
| Health | `GET /api/health` | | `{status, database}`; 503 `database_unavailable` |
| Upload | `POST /api/uploads` | multipart: `files` once per file, `paths` once per file in the same order (relative path, for example `webkitRelativePath`), optional `project_name` | 202 `{company_id, run_id, project_name, accepted: [{relative_path, size}], rejected: [{relative_path, reason}]}`. The run starts at once. |
| Progress | `GET /api/companies/{company_id}/progress` | | `{company_id, project: {state, profile_version, embeddings_stale, running_step, last_error, published_at, confirmed_at, embedded_at, cleaned_at}, run: {run_id, state, started_by, created_at, started_at, finished_at, counts, published, last_error}, documents: [{relative_path, parse_status, extraction_status, pages, warnings, seconds}], active_job, latest_job, recovery: {available, action, reason}, screening: {pending_changes, incomplete_laws, unscreened_laws, stale_decisions, pending}}` |
| Rerun | `POST /api/companies/{company_id}/rerun` | | 202 with the new run. Only before confirmation, and not while a run is active. |
| Draft | `GET /api/companies/{company_id}/draft` | | `{company_id, state, draft_version, draft: {fields: [...]}, open_decisions: [{field, key, kind, cells, allowed}], needs_review, open_hints}`. Only when the latest run is `finished` and the state is `published`. |
| Confirm | `POST /api/companies/{company_id}/confirm` | `{draft_version, decisions: [{field, key?, action, group?, value?, cells?}]}` | Counts (`conflicts_resolved`, `values_typed`, `fields_left_empty`, `rows_dropped`, ...). Embedding and cleanup then run as a background job; poll Progress. |
| Update | `POST /api/companies/{company_id}/profile/update` | `{profile_version, reason?, changes: [{field, kind, row_id? or key?, value?, cells?, end_date?}]}`; `kind` is edit, add, remove or end | `{profile_version, changes, changed_features, embeddings_stale, ...}`. Unless only the contact email changed, a background job refreshes the vectors (and re-screens when the server is configured to); Progress shows `embeddings_stale` and the job until done. |
| Profile | `GET /api/companies/{company_id}/profile` | | `{state, profile_version, embeddings_stale, scalars, scalar_rows, lists: {field: [{id, values, fundstelle, quelle, konfidenz, bestaetigt_von, bestaetigt_am}]}, audit}`. `fundstelle` is filename and page (or the "entered by the advisor" text), `quelle` the cited document (`dateiname`, `titel`, `klasse_code`, `dokument_datum`; empty for advisor-entered values). `lists.finding[].values.normen` holds the finding's norm codes (sorted, `[]` when none); other links are id columns in `values` (for example `veranlagung_id`, `aussenpruefung_id`, `finding_id`) that match the `id` of a row in the corresponding list. Only after confirmation. |
| Companies | `GET /api/companies` | | `{companies: [{company_id, project_name, state, run_state, embeddings_stale, created_at, updated_at}]}` |
| Audit | `GET /api/audit?company_id=&kind=&since=&until=&limit=` | | `{entries: [...]}`, newest first. `kind`: `profile_confirmed`, `profile_changed`, `email_sent`, `template_edited`. |
| Recover | `POST /api/companies/{company_id}/recover` | | 202 `{action, reason, job}` with the job the backend chose from the state, or 200 with `job: null` when nothing is to do. Refused before confirmation (`wrong_state`; use Rerun). |
| Job | `GET /api/jobs/{job_id}` | | `{id, operation, company_id, status, params, result, error, retry_of, created_at, started_at, finished_at}`; `operation`: `law_ingest`, `screen`, `advance`, `refresh`. |
| Retry | `POST /api/jobs/{job_id}/retry` | | 202 with the new attempt (`retry_of` = the old job). Only for `failed` or `interrupted` jobs, once per job. |
| Law upload | `POST /api/laws` | multipart: `file` (one PDF), optional `replace` | 202 with the ingestion job; its `result` holds `law_id`, title, summaries, section count. Same PDF again: 409 `duplicate` (send `replace=true` to replace it). |
| Laws | `GET /api/laws` | | `{laws: [{id, kind, title, reference, published_at, effective_at, status, legal_forms, tax_types, summary, change_summary, filename, ingested_at, sections}]}` |
| Law | `GET /api/laws/{law_id}` | | `{law, sections: [{id, nr, section_ref, heading, page_from, page_to, text, norms, tax_types, model}]}` |
| Screening | `POST /api/screenings` | `{company_id?, law_id?, force?, dry_run?}` (missing company or law: all of them) | 202 with the screening job; its `result.reports` per company: `candidates`, `judged`, `skipped` (reused), `hits`, `deferred`, `unjudged`, `invalidated`, `complete`. A `dry_run` changes nothing. |
| Matches | `GET /api/companies/{company_id}/matches?law_id=&only_hits=&include_history=` | | `{matches: [{id, law_id, law_section_id, section_ref, law_title, objekt_typ, objekt_id, feature_text, score, judge_relevant, judge_direction, judge_reason, is_current, invalidation_reason, ...}]}`; current ones unless `include_history=true`. |
| Alerts | `GET /api/alerts?status=&company_id=&law_id=` | | `{alerts: [{id, status, company_id, match, law, section_ref, section_text, page_from, page_to, recipient_available, outreach, ...}], counts: {new, seen, dismissed, actioned, obsolete}}`. Default: new and seen; repeat `status` for others. |
| Alert | `GET /api/alerts/{alert_id}` | | One alert; reading does not mark it seen. |
| Seen, Dismiss | `POST /api/alerts/{alert_id}/seen`, `POST /api/alerts/{alert_id}/dismiss` | dismiss: optional `{reason}` | The changed alert. |
| Template | `GET /api/email-template`, `POST /api/email-template` | edit: `{subject, body}` | `{subject, body, updated_at}`; the edit is audited. |
| Preview | `GET /api/alerts/{alert_id}/email-preview` | | `{subject, body, recipient, recipient_source, message, sender, mail_mode, redirect_active, template_updated_at}` |
| Send | `POST /api/alerts/{alert_id}/send` | `{recipient, subject, body, confirmed: true, template_updated_at?}` | The stored snapshot `{id, status: sent, recipient, subject, sent_body, sent_at, ...}`; the alert becomes `actioned`. |
| Outreach | `GET /api/outreach?company_id=&alert_id=` | | `{outreach: [...]}`, newest first, every attempt with its status (`pending`, `sent`, `failed`). |

**Decisions (Confirm).** Send exactly one decision per entry of `open_decisions`; nothing is pre-selected. `action` is one of the entry's `allowed` values: `choose` with `group` (index into the field's or row's `groups`, 0-based), `value` with a typed `value`, `leave_empty`, `accept` (list row as is), `edit` with `cells: {column: {group} or {value}}`, `drop` (list row). List rows carry their `key` exactly as the draft shows it.

**Upload example (browser).**

```js
const form = new FormData();
for (const file of input.files) {          // <input type="file" webkitdirectory multiple>
  form.append("files", file, file.name);
  form.append("paths", file.webkitRelativePath);
}
form.append("project_name", "Projekt Weser");  // optional
const answer = await fetch(`${API}/api/uploads`, { method: "POST", body: form });
```

**Errors.** Every error has the body `{code, message, details: [{field, key, message}], error_id, outreach_id}` and carries the CORS headers, so the browser can always read it. Show `message`; show `details` next to the field (or next to the file for uploads). Codes to switch on:

| Code | HTTP | Meaning, what to do |
|---|---|---|
| `validation` | 422 | Invalid body, decision or change; one detail per field. Nothing was changed. |
| `bad_request` | 400 | Malformed request (for example a broken multipart body). |
| `empty_upload` | 400 | No file sent, or every file was rejected (details per file). |
| `upload_too_large` | 413 | The whole upload is over the total limit (placeholder 500 MB). |
| `not_found` | 404 | Unknown company or path. |
| `wrong_state` | 409 | The project state does not allow the call (for example Draft after confirmation, Update before it, Rerun after it). |
| `stale_draft` | 409 | The draft changed since you loaded it: reload the draft. |
| `stale_profile` | 409 | The profile version changed: reload the profile and let the advisor redo the change. |
| `busy` | 409 | A step or background job of this company (or of this law upload) is running: wait for it, then retry. |
| `run_active` | 409 | A run of this company is queued or running: wait for it (keep polling). |
| `duplicate` | 409 | This legal source is stored already; upload again with `replace=true` to replace it. |
| `send_pending` | 409 | An earlier send of this alert is unclear (`outreach_id`): check Gmail "Gesendet" and ask the backend team to resolve it. Nothing was sent now. |
| `already_sent` | 409 | The alert's email was sent already (`outreach_id`). |
| `send_failed` | 502 | The mail provider rejected the email (`outreach_id`); the advisor may send again. |
| `mail_unavailable` | 503 | The mail account is not configured; nothing was sent. |
| `llm_unavailable` | 503 | The server has no OpenAI key; law upload, screening and embedding cannot run. |
| `publish_failed` | 422 | The profile could not be written; the message says why. |
| `not_supported` | 501 | The server runs without a database for this call (development setup). |
| `method_not_allowed` | 405 | Wrong HTTP method. |
| `internal_error` | 500 | Server error; show the message and the `error_id`. |

**Limits (placeholders).** 50 MB per file, 500 MB per upload, 300 files per upload. Unsafe paths (`..`, absolute paths, drive letters, control characters, `:`), duplicate paths (case-insensitive) and hidden or system files (for example `.DS_Store`, `Thumbs.db`) are rejected one by one with a reason; the rest of the upload continues.

## 4. Working without the backend

Mock the responses until the backend exists. Cover at least these cases: all four field statuses, a conflict with three candidates, a group that differs only by rounding, a needs-review value from a scanned page, a failed document, a skipped document and an unsupported file. Now that the backend exists also cover: a confirmed profile with an advisor-entered value, a stale draft error, a stale profile error, a busy error and the "embeddings stale" hint; a failed and an interrupted job with Retry; a screening report with deferred sections; a non-current alert; `send_failed` and `send_pending`.

If a constraint here blocks you or looks wrong, tell the backend team before building around it. Any change will be made in this file first.
