# Mock data: TEMPORARY

Delete this folder when real data from the backend and the screening loop arrives.

- All companies, people, laws, rulings and file names are fictitious. Email domains end in `.example`.
- Every file follows the contract in `screening/contract/` (version `0.1.0`). List files use the envelope `{ schemaVersion, items }`.
- Only the adapter (`src/data/mock/`) imports these files. Pages never do.

| File | Content |
|---|---|
| `advisors.json` | One advisor `adv-001` with read state |
| `companies.json` | 5 clients with different edge cases (many facts, no email, few facts, unknown fact type, nested extra) |
| `lawChanges.json` | 6 law changes (bill, BMF letter, law, ruling) |
| `matches.json` | 14 matches covering every label and email status combination |
| `outreach.json` | Sent emails |
| `auditLog.json` | Seed history entries |
| `uploadRun.json` | A finished run with ok, partial, failed, skipped and unsupported documents |
| `profileDraft.json` | A draft with all four field statuses, a three-way conflict, a rounding group, an OCR value |
