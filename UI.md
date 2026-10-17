# UI integration guide

For the UI team. This lists the features the UI must have and the constraints it must follow so it integrates smoothly with the backend. Exact data shapes (including the profile draft) will come from the database schema and the API once they are pushed; this file does not define them.

**Backend status:**
- Built: document parsing, one extraction agent per document, and the merge into a profile draft (steps 1 to 7). The draft can be published into the database schema `profil` as unconfirmed rows.
- Not built yet: advisor confirmation (step 8), embeddings, laws, matching, alerts, email, and the HTTP API. Anything below marked *proposed* is final only after the backend team confirms it.
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
4. **Later (not specified yet):** alerts with the matched feature and law section, an email confirm-and-edit screen, in-app calendar. The email starts from **one fixed template, the same for every client**, not drafted per company or law; only the recipient differs. The screen shows the template text, the advisor **can edit it before sending** (the edit applies to that one email only, the template stays unchanged) and confirms to send.

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
14. **IDs are opaque strings.** Do not parse or build them. `company_id` identifies one client company and tags all its data so clients never mix. In the database it is the deal's project name (`profil.deal.projektname`; the CLI defaults it to the folder name). *Proposed:* the upload step returns it and the UI sends it on every later call. Who creates it at upload is still open.
15. **Upload limits:** supported files are PDF, xlsx and xlsm. Everything else is accepted but reported as unsupported. Size limit per file is 50 MB (placeholder). Do not block the upload on file type; let the backend report it.
16. **Progress by polling** every 1 to 2 seconds while a run is active (*proposed*). No websockets needed.
17. **Errors** arrive as an object with a machine-readable code and a human-readable message. Show the message.
18. **CORS:** tell the backend team your dev URL and port so the backend allows it.
19. **Language:** documents are German. Quotes and extracted text values are shown exactly as in the document. The UI language is your decision.

## 3. Proposed calls (names only, shapes to follow)

| Call | Purpose |
|---|---|
| Upload | Send the files of the folder; returns the company id and the accepted and rejected files. |
| Progress | Run state plus a status record per document. |
| Draft | The profile draft for review. Available when the run is finished. |
| Confirm | The advisor's final choice per field. Saves the confirmed profile. |

## 4. Working without the backend

Mock the responses until the backend exists. Cover at least these cases: all four field statuses, a conflict with three candidates, a group that differs only by rounding, a needs-review value from a scanned page, a failed document, a skipped document and an unsupported file.

If a constraint here blocks you or looks wrong, tell the backend team before building around it. Any change will be made in this file first.
