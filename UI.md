# UI integration guide

For the UI team. This lists the features the UI must have and the constraints it must follow so it integrates smoothly with the backend. Exact data shapes (including the profile draft) will come from the database schema and the API once they are pushed; this file does not define them.

**Backend status:**
- Built: document parsing (reading the uploaded PDF and Excel files).
- Planned, not built yet: extraction, merge, the API. Anything below marked *proposed* is final only after the backend team confirms it.
- No authentication in the prototype. Use mock data only, no real client data.

Process diagram: `Project Diagram.mmd`. Step numbers: `Project Brief.md` section 1.

## 1. Features the UI must have

1. **Upload:** pick the DD folder and send all files, keeping each file's relative path.
2. **Progress:** while the backend works, show one row per document with its status and warnings. Failed, skipped and unsupported files stay visible after the run.
3. **Profile review (step 8):** the advisor checks the company profile before it is saved:
   - see every profile field with its value, or its candidate values when documents disagree;
   - see the source of every value (filename, page, evidence quote);
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
6. **Later (not specified yet):** alerts with the matched feature and law section, an email confirm-and-edit screen, in-app calendar. The email starts from **one fixed template, the same for every client**, not drafted per company or law. The screen shows the template text; the advisor **can edit text, subject and recipient before sending**. The backend sends exactly what the UI submits and stores every sent email (edited or not) as a snapshot; sends and template edits also appear in the audit log. Editing one email never changes the template; the template changes only through a separate explicit "edit template" screen.

## 2. Constraints the UI must follow

**Fields and values**
1. **Never hardcode profile fields.** The field list comes from a schema delivered by another team and will change. Render the review screen from whatever fields the backend sends.
2. **Respect field types.** Types are string, integer, number, boolean, date and enum. Numbers are plain JSON numbers: format them for display only (German style, for example 1.234,50) and never parse a formatted string back. Dates are ISO strings (2023-12-31). Enum inputs must offer only the allowed values.
3. **Values are validated by the backend before saving.** Show its error messages next to the fields; do not assume your own validation is enough.

**Sources and review**
4. **Always show the source of a value:** filename, page and evidence quote. No value is displayed without it. For an Excel file the page is the sheet's position in the workbook.
5. **Show review flags.** Values from scanned pages (OCR) or with unverified evidence are marked needs-review. Make that visible.
6. **Evidence quotes are untrusted text from documents.** Render them as plain text, never as HTML or markdown.
7. **No "open original document".** Raw documents are deleted after processing; show the quote and page number only.

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
14. **IDs are opaque strings.** Do not parse or build them. `company_id` identifies one client company and tags all its data so clients never mix; *proposed:* the backend creates it at upload and returns it, and the UI sends it on every later call.
15. **Upload limits:** supported files are PDF, xlsx and xlsm. Everything else is accepted but reported as unsupported. Size limit per file is 50 MB (placeholder). Do not block the upload on file type; let the backend report it.
16. **Progress by polling** every 1 to 2 seconds while a run is active (`run.state` `queued` or `running`), and after Confirm until the project state is `cleaned` (or `last_error` is set). No websockets.
17. **Errors** arrive as an object with a machine-readable code and a human-readable message. Show the message.
18. **CORS:** tell the backend team your dev URL and port so the backend allows it (allowed now: `http://localhost:3000`, `http://localhost:5173` and the same on `127.0.0.1`).
19. **Language:** documents are German. Quotes and extracted text values are shown exactly as in the document. The UI language is your decision.

**Confirmed profile, updates and email**
20. **Profile version.** The confirmed profile has a version number (starts at 1, +1 per update). Send the version you saw with every update; if the backend answers "stale profile", reload and let the advisor redo the change.
21. **Update rules.** Required fields cannot be emptied. Values typed by the advisor are validated like any other (type, enum values, length, date format); show the per-field messages. An update is refused before the profile is confirmed, while the company is busy (confirm, embedding or cleanup is running), or when the version is stale; the error codes tell these apart.
22. **Advisor-entered values.** After an update the citation of a changed value is "entered by the advisor" with the date, not a filename. Do not invent a document source for it.
23. **Project state vocabulary:** published (draft saved, waiting for review), confirmed, embedded, cleaned; plus the flag "embeddings stale". Show them as plain status text.
24. **Confirm before saving an update.** Show a summary of the changes and an explicit "are you sure" step before calling Update. There is no undo; the advisor corrects a mistake with a new update.
25. **Email.** Send the final recipient, subject and text from the screen; the backend sends exactly that and never the stored template. Show the template text as the starting point only. Never write edits back to the template from the email screen.

26. **Single user.** The platform serves one Steuerberater: there is no login and no person field in any call. You can still get "stale" and "busy" errors (two browser tabs, a double click, a background step still running); reload when that happens.

## 3. Calls

All paths start with `/api`. Ids are opaque strings (URL-encode them in paths). Bodies and responses are JSON except Upload. No call takes a person: the backend stamps "Steuerberater" itself. Responses are never cached (`Cache-Control: no-store`); every response carries an `X-Request-ID` header.

| Call | Method and path | Body | Answer |
|---|---|---|---|
| Health | `GET /api/health` | | `{status, database}`; 503 `database_unavailable` |
| Upload | `POST /api/uploads` | multipart: `files` once per file, `paths` once per file in the same order (relative path, for example `webkitRelativePath`), optional `project_name` | 202 `{company_id, run_id, project_name, accepted: [{relative_path, size}], rejected: [{relative_path, reason}]}`. The run starts at once. |
| Progress | `GET /api/companies/{company_id}/progress` | | `{company_id, project: {state, profile_version, embeddings_stale, running_step, last_error, published_at, confirmed_at, embedded_at, cleaned_at}, run: {run_id, state, started_by, created_at, started_at, finished_at, counts, published, last_error}, documents: [{relative_path, parse_status, extraction_status, pages, warnings, seconds}]}` |
| Rerun | `POST /api/companies/{company_id}/rerun` | | 202 with the new run. Only before confirmation, and not while a run is active. |
| Draft | `GET /api/companies/{company_id}/draft` | | `{company_id, state, draft_version, draft: {fields: [...]}, open_decisions: [{field, key, kind, cells, allowed}], needs_review, open_hints}`. Only when the latest run is `finished` and the state is `published`. |
| Confirm | `POST /api/companies/{company_id}/confirm` | `{draft_version, decisions: [{field, key?, action, group?, value?, cells?}]}` | Counts (`conflicts_resolved`, `values_typed`, `fields_left_empty`, `rows_dropped`, ...). Embedding and cleanup then run in the background; poll Progress. |
| Update | `POST /api/companies/{company_id}/profile/update` | `{profile_version, reason?, changes: [{field, kind, row_id? or key?, value?, cells?, end_date?}]}`; `kind` is edit, add, remove or end | `{profile_version, changes, changed_features, embeddings_stale, ...}`. The vectors refresh in the background; Progress shows `embeddings_stale` until done. |
| Profile | `GET /api/companies/{company_id}/profile` | | `{state, profile_version, embeddings_stale, scalars, scalar_rows, lists: {field: [{id, values, fundstelle, quelle, konfidenz, bestaetigt_von, bestaetigt_am}]}, audit}`. `fundstelle` is filename and page (or the "entered by the advisor" text), `quelle` the cited document (`dateiname`, `titel`, `klasse_code`, `dokument_datum`; empty for advisor-entered values). Only after confirmation. |
| Companies | `GET /api/companies` | | `{companies: [{company_id, project_name, state, run_state, embeddings_stale, created_at, updated_at}]}` |
| Audit | `GET /api/audit?company_id=&kind=&since=&until=&limit=` | | `{entries: [...]}`, newest first. `kind`: `profile_confirmed`, `profile_changed`, `email_sent`, `template_edited`. |
| Reserved | | | Alerts, calendar, and the email calls (template read and edit, preview, send) come with steps 16 to 19. |

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

**Errors.** Every error has the body `{code, message, details: [{field, key, message}], error_id}`. Show `message`; show `details` next to the field (or next to the file for uploads). Codes to switch on:

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
| `busy` | 409 | Confirm, embedding or cleanup of this company is running: retry shortly. |
| `run_active` | 409 | A run of this company is queued or running: wait for it (keep polling). |
| `publish_failed` | 422 | The profile could not be written; the message says why. |
| `not_supported` | 501 | The server runs without a database for this call (development setup). |
| `method_not_allowed` | 405 | Wrong HTTP method. |
| `internal_error` | 500 | Server error; show the message and the `error_id`. |

**Limits (placeholders).** 50 MB per file, 500 MB per upload, 300 files per upload. Unsafe paths (`..`, absolute paths, drive letters, control characters, `:`), duplicate paths (case-insensitive) and hidden or system files (for example `.DS_Store`, `Thumbs.db`) are rejected one by one with a reason; the rest of the upload continues.

## 4. Working without the backend

Mock the responses until the backend exists. Cover at least these cases: all four field statuses, a conflict with three candidates, a group that differs only by rounding, a needs-review value from a scanned page, a failed document, a skipped document and an unsupported file. Now that the backend exists also cover: a confirmed profile with an advisor-entered value, a stale draft error, a stale profile error, a busy error and the "embeddings stale" hint.

If a constraint here blocks you or looks wrong, tell the backend team before building around it. Any change will be made in this file first.
