# UI integration guide

For the UI team. This lists the features the UI must have and the constraints it must follow so it integrates smoothly with the backend. Exact data shapes (including the profile draft) will come from the API once it is built: the backend will publish an OpenAPI file (`openapi.json`) you can generate mocks and types from. This file does not define the shapes.

**Backend status:**
- Built: document parsing, one extraction agent per document, and the merge into a profile draft (steps 1 to 7). The draft can be published into the database schema `profil` as unconfirmed rows.
- Planned (plans written, not built yet): the HTTP API and upload layer, advisor confirmation, updating a confirmed profile, embeddings and cleanup (steps 0 and 8 to 10).
- Not planned in detail yet: laws, matching, alerts, email sending, calendar (steps 11 to 19). Law fetching is handled by another team.
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
   - see the audit log: who changed what and when (field, old value, new value, reason), plus confirmations, sent emails and template edits;
   - see a hint "embeddings stale" while the backend refreshes the search data after a change.
6. **Later (not specified yet):** alerts with the matched feature and law section, an email confirm-and-edit screen, in-app calendar. The email starts from **one fixed template, the same for every client**, not drafted per company or law. The screen shows the template text; the advisor **can edit text, subject and recipient before sending**. The backend sends exactly what the UI submits and stores every sent email (edited or not) as a snapshot with the acting person; sends and template edits also appear in the audit log. Editing one email never changes the template; the template changes only through a separate explicit "edit template" screen.

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
    - Parsing: ok, partial (usable, with warnings), failed, unsupported.
    - Extraction: pending, ok, partial, failed, skipped, write_failed.
    - Profile field: confirmed (two or more documents agree), single_source (one document), conflict (different values), missing (nothing found; stored as `unbekannt` in the database).
13. **Unknown values must not crash the UI.** If the backend sends a status or type you do not know, show it as plain text.

**Technical**
14. **IDs are opaque strings.** Do not parse or build them. `company_id` identifies one client company and tags all its data so clients never mix. In the database it is the deal's project name (`profil.deal.projektname`; the CLI defaults it to the folder name). The backend creates it at upload (from the optional project name the UI sends, made unique) and returns it; the UI sends it on every later call.
15. **Upload limits:** supported files are PDF, xlsx and xlsm. Everything else is accepted but reported as unsupported. Size limit per file is 50 MB (placeholder). Do not block the upload on file type; let the backend report it.
16. **Progress by polling** every 1 to 2 seconds while a run is active (*proposed*). No websockets needed.
17. **Errors** arrive as an object with a machine-readable code and a human-readable message. Show the message.
18. **CORS:** tell the backend team your dev URL and port so the backend allows it.
19. **Language:** documents are German. Quotes and extracted text values are shown exactly as in the document. The UI language is your decision.

**Confirmed profile, updates and email**
20. **Profile version.** The confirmed profile has a version number (starts at 1, +1 per update). Send the version you saw with every update; if the backend answers "stale profile", reload and let the advisor redo the change.
21. **Update rules.** Required fields cannot be emptied. Values typed by the advisor are validated like any other (type, enum values, length, date format); show the per-field messages. An update is refused before the profile is confirmed, while the company is busy (confirm, embedding or cleanup is running), or when the version is stale; the error codes tell these apart.
22. **Advisor-entered values.** After an update the citation of a changed value is "entered by the advisor" with the date, not a filename. Do not invent a document source for it.
23. **Project state vocabulary:** published (draft saved, waiting for review), confirmed, embedded, cleaned; plus the flag "embeddings stale". Show them as plain status text.
24. **Confirm before saving an update.** Show a summary of the changes and an explicit "are you sure" step before calling Update. There is no undo; the advisor corrects a mistake with a new update.
25. **Email.** Send the final recipient, subject and text from the screen; the backend sends exactly that and never the stored template. Show the template text as the starting point only. Never write edits back to the template from the email screen.

26. **Acting person.** Every call that changes data (confirm, update, send email, edit template) carries the name of the acting advisor, and the audit log shows it. There is no login yet, so the name is typed or chosen; when login arrives the backend takes the identity from it and the field goes away. Several advisors may work on the same profile: expect "stale" and "busy" errors and reload.

## 3. Proposed calls (names only, shapes to follow)

| Call | Purpose |
|---|---|
| Upload | Send the files of the folder; returns the company id and the accepted and rejected files. |
| Progress | Run state plus a status record per document. |
| Rerun | Re-process the stored files of a company (before confirmation only). |
| Draft | The profile draft for review. Available when the run is finished. |
| Confirm | The advisor's final choice per field, with the draft version. Saves the confirmed profile; embedding and cleanup then run in the background. |
| Update | Changes to a confirmed profile, with the profile version, the person's name and an optional reason. |
| Audit | The audit log, filterable by company, kind, person and date, newest first. |
| Companies | The client list with name, state and timestamps. |
| Profile | The confirmed profile with citations and its version. |
| Reserved | Alerts, calendar, and the email calls (template read and edit, preview, send) come with steps 16 to 19. |

Error codes you can switch on (names may change, the list will be in the OpenAPI file): wrong state, busy, stale draft (reload the draft), stale profile (reload the profile), validation (with per-field details).

## 4. Working without the backend

Mock the responses until the backend exists. Cover at least these cases: all four field statuses, a conflict with three candidates, a group that differs only by rounding, a needs-review value from a scanned page, a failed document, a skipped document and an unsupported file. After the backend exists also cover: a confirmed profile with an advisor-entered value, a stale draft error, a stale profile error, a busy error and the "embeddings stale" hint.

If a constraint here blocks you or looks wrong, tell the backend team before building around it. Any change will be made in this file first.
