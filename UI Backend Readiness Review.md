# UI/backend readiness review: steps 0–18

Reviewed 2026-10-17 against `CLAUDE.md`, the project brief, diagram and implementation
plans. Calendar remains excluded. This review leaves application code unchanged.

The upload/review/profile happy path is available over HTTP. The complete flow through
matching, alerts and email is not ready for UI integration: those operations currently
exist only as services and CLI commands, and the correctness/recovery findings below
need attention before connecting the whole workflow.

Validation: all **365 existing tests passed**, with local Postgres integration enabled;
Ruff lint and format checks passed. The checked-in OpenAPI exactly matches the app's
generated schema. Additional isolated probes reproduced the findings described below;
passing the existing suite does not cover these cases. Probes used fake LLM responses
and temporary mock files; the profile-projection check read an existing test profile
from local Postgres. No real email was sent.

| Steps | Existing implementation | UI readiness |
|---|---|---|
| 0–7: upload, parse, extract, merge/publish | HTTP upload, progress and rerun; parallel parser/extractor; provenance and deterministic merge | Happy path available; draft persistence must fail visibly (finding 8) |
| 8–10: review, confirm, embed, clean up; manual profile updates | HTTP draft, confirm, profile, update and audit; background embedding/cleanup | Happy path available; cleanup/recovery, profile projection and browser errors need fixes |
| 11–15: law ingestion, section embeddings, matching | Services, CLI, stored source explanations and match results | HTTP contract absent; matching invalidation needs correction |
| 16–18: alerts, template, confirmed email | Services, CLI, durable pending reservation, snapshots, audit, log/SMTP transports | HTTP contract absent; real Gmail acceptance check still requires account setup |

## Findings, ordered by priority

### 1. [P1] No HTTP contract for steps 11–18

Source: `backend/app/api/app.py:89`; `backend/openapi.json`.

The app registers only system, upload/progress, review and profile routers. Its ten API
paths expose no laws/sections, match results, alerts, template editing, email preview,
confirmed send or outreach history. This was deferred by the implementation plans; it
now blocks those UI screens regardless of the services' test coverage.

Required: add thin routes over the existing services/repository, define response shapes
and safe validation/state/provider errors, and regenerate OpenAPI. Add HTTP tests for the
complete alert-to-confirmed-send flow, including pending, failed and already-sent cases.
Calendar stays excluded.

### 2. [P1] Cleanup can report success while raw DD files remain

Sources: `backend/app/services/cleanup.py:90`, `backend/app/db/postgres.py:581`,
`backend/app/runs/manager.py:234`. Memory storage follows the same folder-clearing behavior.

Cleanup first commits deletion of working rows and clears `pipeline.runs.folder`, then
attempts filesystem deletion. If a file is locked or deletion otherwise fails, the
project remains embedded but its durable upload path is lost. A later advance collects
no folders, verifies an empty target list and marks the project cleaned while the raw
files still exist. A crash between these operations has the same recovery problem.

Reproduced: simulated file deletion failure yielded `folder=null`, raw directory present,
state `embedded`; retrying the actual manager advance yielded state `cleaned` with that
directory still present.

Required: retain durable deletion targets until file deletion and verification finish.
Clear folder references only after success, or persist a separate cleanup work record.
Test filesystem failure and process interruption followed by restart/retry.

### 3. [P1] Removing a feature can leave its old alert active and sendable

Source: `backend/app/services/screen.py:147` and `:178`.

Re-screening only writes matches for sections that still pass the rule/vector stages.
When a feature disappears, a legal-form/tax filter excludes a section, or its score drops
below the threshold, the old match never receives an update. Alert obsolescence only
runs when a new negative match is stored, so the old positive alert survives.

Reproduced: screen one relevant feature, remove all company features, then screen again.
The second run had zero candidates and zero stored updates, but one positive match and
one `new` alert remained. The advisor can still prepare/send email for that stale alert.

Required: reconcile previous matches against the current screening scope and invalidate
results that no longer qualify, preserving dismissed/actioned history. Test feature
removal, empty feature sets, filter exclusions and below-threshold scores.

### 4. [P1] Match reuse ignores changed judge inputs and newly stronger features

Source: `backend/app/services/screen.py:57` and `:178`.

`needs_judging` compares only the old matched feature's key and sentence. It ignores
years, `offen`, norms, tax types and the current candidate features, although these are
judge inputs. An unchanged old sentence also suppresses judging a newly added feature
that is now the best candidate for the section.

Reproduced separately:

- Changing years/openness and incrementing the profile version skipped judging and kept
  the old positive result/version.
- Adding a new, higher-scoring feature after a negative result produced best candidate
  `finding:2`, but skipped judging and retained the old negative on `finding:1`.

Required: compare a fingerprint of the complete relevant judge inputs/candidate set, or
otherwise invalidate affected section decisions when these inputs change. Preserve the
zero-call optimization for genuinely unchanged inputs. Add tests for both examples.

### 5. [P2] HTTP profile updates bypass configured re-screening

Sources: `backend/app/api/routes/profile.py:92`, `backend/app/runs/manager.py:245`,
`backend/app/services/update.py:65`.

The HTTP update commits with `refresh=False`, then starts `RunManager._refresh`. The
`SCREEN_AFTER_UPDATE` behavior lives after the synchronous refresh in the update service,
which that early return bypasses. The background manager refreshes vectors but never
screens pending changes. Even enabling the setting leaves matches/alerts unchanged.

Reproduced: with `screen_after_update=True`, the actual manager background method awaited
refresh once and screening zero times.

Required: share the post-update workflow between CLI and HTTP; after a successful
background refresh, invoke the configured pending screening and expose its progress/error.
Add an HTTP test that changes a relevant fact and observes the updated match/alert.

### 6. [P2] Failed or interrupted post-confirmation work has no UI recovery path

Sources: `backend/app/runs/manager.py:71`, `:122`, `:223`;
`backend/app/db/profil_updater.py:137`.

An embedding/cleanup failure after confirmation leaves the project confirmed, embedded
or stale. Confirm cannot be repeated, rerun is restricted to pre-confirmation, and no
HTTP action retries advance/refresh/cleanup. Startup only interrupts document runs; it
does not resume those post-confirmation tasks. A hard interruption can also leave
`running_step` set: updates reject any marker without checking its age, while only a new
step acquisition can reclaim an expired marker.

Required: provide a state-checked, idempotent recovery action and reconcile abandoned
background work on startup. Keep email's uncertain pending resolution deliberate; do not
automatically retry an email. Test exhausted embedding retries, cleanup errors and a
restart with an abandoned step marker.

### 7. [P2] Browser clients cannot read some structured error responses

Sources: `backend/app/api/app.py:91`, `backend/app/api/middleware.py:42`,
`backend/app/api/errors.py:168`.

The upload limit middleware returns early outside CORS. Unhandled 500 responses are also
built outside the CORS/request-context middleware. An allowed frontend origin therefore
sees a browser CORS failure instead of the documented JSON error. Unexpected 500s also
lose the response request-id and no-store headers.

Reproduced with `Origin: http://localhost:3000`: normal health returned the allow-origin
header; an oversized upload returned 413 without it; a simulated unexpected error
returned 500 without allow-origin, X-Request-ID or Cache-Control.

Required: apply CORS and response-context headers to early errors and unexpected server
errors. Test both 413 and 500 with an allowed browser origin, not just success responses.

### 8. [P2] A failed draft write can still finish the upload as published

Source: `backend/app/agents/orchestrator.py:171`.

`merge` logs and suppresses `save_profile_draft` failures, then returns the in-memory
draft. The manager publishes it and marks the run finished/published. Draft/review and
confirmation require the persisted draft, so the UI is told the run succeeded but cannot
review it; on a rerun it could instead retrieve an earlier persisted draft.

Reproduced with a repository that fails only draft persistence: run `finished`,
`published=true`, project `published`, stored draft absent.

Required: fail the run if draft persistence fails, or atomically persist draft and its
publication before advertising review readiness. Add a transient-draft-write failure test.

### 9. [P2] Confirmed-profile reads omit stored linked facts needed by the UI

Source: `backend/app/db/profil_updater.py:424`.

The read returns raw rows of each profile table without reconstructing linked schema
fields. Finding norms live in `profil.finding_norm`, so the confirmed profile loses the
`normen` cell shown during review. That association table is not included elsewhere in
the response, so the UI cannot display these stored norms from the existing contract.

Postgres check: a stored test finding had norms § 10a GewStG and § 8c KStG; its confirmed
profile `finding.values` had no `normen` key. The stored profile snapshot reader can read
them, so this is a response-projection gap rather than lost stored data.

Required: join/aggregate finding norms back into the confirmed-profile contract, or expose
an explicit normalized relationship contract. Test review-to-confirmed-profile continuity
for the selected norms; document how the UI resolves other linked metadata.

## Suggested order before connecting the complete UI

1. Fix cleanup durability, match reconciliation/cache invalidation and draft persistence.
2. Complete the API/background workflow and add recovery actions, browser error handling
   and the linked profile projection.
3. Expose steps 11–18 through HTTP; regenerate OpenAPI and run full HTTP acceptance tests
   with Postgres and fake/log providers. Freeze that contract for the frontend.
4. Review the fixed email wording/signature, configure the sender and own-inbox redirect,
   and perform the pending live Gmail acceptance check when account details are available.

The frontend can begin upload/progress and draft-review integration against the current
contract while these items are addressed. The full matching/alert/email workflow should
wait for the missing contract and the correctness fixes. Calendar remains outside scope.
