# Data contract

Shared shapes between the screening loop, the frontend and later the backend. JSON Schema, draft 2020-12. Current version: `0.1.0`.

| File | Entity |
|---|---|
| `common.schema.json` | Shared parts: id, source, fact, candidate, error, extra |
| `company.schema.json` | Client: thin core, open `facts`, free `extra` |
| `law_change.schema.json` | Law change with sections and detail blocks |
| `match.schema.json` | One per company and law change: `assessment` (screening) and `review` (app) |
| `outreach.schema.json` | Snapshot of one sent email per client |
| `advisor.schema.json` | Advisor and their read state |
| `audit_entry.schema.json` | History entry (Verlauf) |
| `upload_run.schema.json` | Document processing progress |
| `profile_draft.schema.json` | Draft profile for review |
| `collection.schema.json` | Envelope for list files: `schemaVersion` plus `items` |

Rules:
- IDs are opaque strings.
- Open vocabularies (status, type, kind, label) are plain strings. Consumers render unknown values as text.
- Fact labels are never hardcoded in consumers.
- Screening writes only `match.assessment`. The app writes only `match.review`.
