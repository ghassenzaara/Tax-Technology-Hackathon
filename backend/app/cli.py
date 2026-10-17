"""Parse a folder of DD documents, optionally extract and merge a profile draft.

Usage:
  uv run python -m app.cli <folder> [--json out.json] [--markdown] [--log-level INFO]
  uv run python -m app.cli <folder> --extract [--company-id PROJECT] [--schema FILE]
                           [--repository memory|postgres] [--publish] [--json out.json]
  uv run python -m app.cli --merge-only --company-id PROJECT [--publish]
  uv run python -m app.cli --init-db [--seed] [--reset-profil] --repository postgres
  uv run python -m app.cli --review --company-id PROJECT [--json review.json]
  uv run python -m app.cli --confirm decisions.json --confirmed-by NAME --company-id PROJECT
  uv run python -m app.cli --embed | --cleanup | --advance --company-id PROJECT
                           [--delete-path DIR ...]
  uv run python -m app.cli --profile | --audit [--kind KIND] [--person NAME] --company-id PROJECT
  uv run python -m app.cli --update changes.json --changed-by NAME --company-id PROJECT
  uv run python -m app.cli --ingest-law FILE.pdf [--replace] [--screen-after-ingest]
  uv run python -m app.cli --laws
  uv run python -m app.cli --screen [--company-id P] [--law-id N] [--dry-run] [--force]
  uv run python -m app.cli --screen-pending --company-id PROJECT
  uv run python -m app.cli --matches --company-id PROJECT [--only-hits] [--json out.json]

The company id is the deal's project name (profil.deal.projektname). --publish writes the
draft into schema profil as unconfirmed rows, replacing an earlier publish of that project
(refused once the project is confirmed).

Steps 8 to 10 (postgres only): --review prints the open decisions and the draft version
(--json also writes a decisions template); --confirm applies a decisions file (format:
app/models/review.py ConfirmRequest) and stamps every row; --embed writes one vector per
feature; --cleanup deletes the working rows and the given upload paths (inside DATA_DIR);
--advance runs embed then cleanup (and repairs stale vectors). Confirm is never chained: it
needs the advisor. After confirmation --update applies a changes file (format:
app/models/update.py UpdateRequest) and refreshes the affected vectors; --profile and
--audit print the confirmed profile and the append-only audit log.

Steps 11 to 15 (postgres only): --ingest-law parses, splits and embeds one law PDF into
schema screening (the same file twice is refused, --replace replaces it); --screen runs the
rule filter, the vector score and the LLM judge for one or every embedded company and
stores the matches (--dry-run stops before the judge and stores nothing); --screen-pending
re-screens a company after advisor updates; --matches lists the stored matches.

Known limit: files written with --json or --markdown contain document text and are outside
the pipeline's control; step 10 does not delete them.

Exit code is non-zero only when the run cannot start (bad folder, bad schema file, missing
key or database settings) or a requested publish or step fails, not when single files fail.
"""

import argparse
import asyncio
import json
import logging
import sys
from collections.abc import AsyncIterator
from pathlib import Path
from time import perf_counter

from app.agents.orchestrator import ExtractionRun, Orchestrator
from app.config import Settings, get_settings
from app.db.repository import (
    PublishError,
    PublishReport,
    Repository,
    RepositoryError,
    StateError,
    make_repository,
)
from app.ingest.pipeline import DocumentParser, RunSummary, summarize
from app.llm import make_client
from app.logging_setup import configure_logging
from app.matching.score import MatchError
from app.models.audit import AuditEntry, AuditFilter, AuditKind
from app.models.document import DocStatus, Document
from app.models.extraction import DocumentExtraction
from app.models.law import IngestReport, Law
from app.models.match import Match, ScreenReport
from app.models.profile import FieldResult, FieldStatus, ProfileDraft
from app.models.review import ConfirmRequest, ConfirmResult, ReviewView
from app.models.update import ConfirmedProfile, UpdateRequest, UpdateResult
from app.profile.review import DecisionError
from app.profile.schema import ProfileSchema, SchemaError, load_profile_schema
from app.services.cleanup import CleanupError, cleanup_project
from app.services.confirm import confirm_profile, review_profile
from app.services.embed import Embedder, EmbedError
from app.services.law_ingest import IngestError, ingest_law
from app.services.screen import Screener, ScreenError
from app.services.update import read_profile, update_profile

log = logging.getLogger("app.cli")


def main(argv: list[str] | None = None) -> int:
    args = _parse_args(argv)
    configure_logging(args.log_level)

    updates = {}
    if args.schema:
        updates["profile_schema_path"] = Path(args.schema)
    if args.repository:
        updates["repository"] = args.repository
    settings = get_settings().model_copy(update=updates)

    db_setup = args.init_db or args.seed or args.reset_profil
    if any(getattr(args, command) for command in SCREEN_COMMANDS):
        return _run_screening(args, settings)
    if any(getattr(args, command) for command in STEP_COMMANDS):
        return _run_steps(args, settings)
    needs_folder = not (args.merge_only or (db_setup and not args.folder))
    folder = Path(args.folder) if args.folder else None
    if needs_folder and (folder is None or not folder.is_dir()):
        return _fail(f"not a folder: {folder}" if folder else "a folder is required")
    if args.merge_only and not args.company_id:
        return _fail("--merge-only needs --company-id")
    if args.extract and settings.openai_api_key is None:
        return _fail("--extract needs OPENAI_API_KEY")
    if (db_setup or args.publish) and settings.repository != "postgres":
        return _fail("--init-db, --seed, --reset-profil and --publish need --repository postgres")
    if args.publish and not (args.extract or args.merge_only):
        return _fail("--publish needs --extract or --merge-only")

    schema = repository = None
    if args.extract or args.merge_only or db_setup:
        try:
            schema = load_profile_schema(settings.profile_schema_path)
            repository = make_repository(settings)
        except (SchemaError, RepositoryError) as exc:
            return _fail(str(exc))

    # Default: the folder name, so reruns on the same folder replace instead of piling up.
    # TODO: the upload layer creates the id.
    company_id = args.company_id or (folder.resolve().name if folder else "")
    loop_factory = None
    if sys.platform == "win32" and settings.repository == "postgres":
        loop_factory = asyncio.SelectorEventLoop  # psycopg async needs a selector loop

    if not needs_folder:
        draft, published = asyncio.run(
            _db_only(settings, schema, repository, company_id, args), loop_factory=loop_factory
        )
        if draft is not None:
            _print_draft(draft)
            _write_json(args.json, {"profile_draft": draft.model_dump(mode="json")})
        return _print_publish(published)

    documents, summary, run, published = asyncio.run(
        _run(folder, settings, schema, repository, company_id, args), loop_factory=loop_factory
    )
    documents.sort(key=lambda doc: doc.relative_path)
    log.info(summary.headline())

    _print_report(documents, summary)
    if args.markdown:
        for doc in documents:
            print("\n" + doc.to_markdown())
    if run is not None:
        _print_extractions(run)
        _print_draft(run.draft)
        print(f"\nParse: {summary.headline()}. Extraction: {run.summary.headline()}.")
    payload = {
        "summary": summary.model_dump(mode="json"),
        "documents": [doc.model_dump(mode="json") for doc in documents],
    }
    if run is not None:
        payload["extraction_summary"] = run.summary.model_dump(mode="json")
        payload["extractions"] = [e.model_dump(mode="json") for e in run.extractions]
        payload["profile_draft"] = run.draft.model_dump(mode="json")
    _write_json(args.json, payload)
    return _print_publish(published)


async def _run(
    folder: Path,
    settings: Settings,
    schema: ProfileSchema | None,
    repository: Repository | None,
    company_id: str,
    args: argparse.Namespace,
) -> tuple[list[Document], RunSummary, ExtractionRun | None, PublishReport | str | None]:
    parser = DocumentParser(settings)
    documents: list[Document] = []
    start = perf_counter()
    parse_end = start

    async def parsed() -> AsyncIterator[Document]:
        nonlocal parse_end
        async for doc in parser.parse_folder(folder):
            documents.append(doc)
            yield doc
        parse_end = perf_counter()

    run = published = None
    if args.extract:
        try:
            await repository.initialize()  # pipeline tables, idempotent
            await _setup_db(repository, settings, args)
            orchestrator = Orchestrator(schema, repository, settings, client=parser.client)
            run = await orchestrator.run(parsed(), company_id)  # agents start as docs arrive
            if args.publish:
                published = await _publish(
                    orchestrator, run.draft, run.extractions, run_scoped=True
                )
        finally:
            await repository.close()
    else:
        if repository is not None:  # parse only, but the database was asked to be set up
            try:
                await _setup_db(repository, settings, args)
            finally:
                await repository.close()
        async for _ in parsed():
            pass
    return documents, summarize(documents, parse_end - start), run, published


async def _db_only(
    settings: Settings,
    schema: ProfileSchema,
    repository: Repository,
    company_id: str,
    args: argparse.Namespace,
) -> tuple[ProfileDraft | None, PublishReport | str | None]:
    try:
        await _setup_db(repository, settings, args)
        if not args.merge_only:
            return None, None
        orchestrator = Orchestrator(schema, repository, settings)
        draft = await orchestrator.merge(company_id)
        published = await _publish(orchestrator, draft, []) if args.publish else None
        return draft, published
    finally:
        await repository.close()


STEP_COMMANDS = ("review", "confirm", "embed", "cleanup", "advance", "profile", "update", "audit")


def _run_steps(args: argparse.Namespace, settings: Settings) -> int:
    """Steps 8 to 10 and profile updates on a published project. Database only."""
    chosen = [a for a in STEP_COMMANDS if getattr(args, a)]
    if len(chosen) > 1:
        return _fail(f"choose one command at a time: {', '.join('--' + c for c in chosen)}")
    command = chosen[0]
    if settings.repository != "postgres":
        return _fail(f"--{command} needs --repository postgres")
    if not args.company_id:
        return _fail(f"--{command} needs --company-id")
    if args.confirm and not args.confirmed_by:
        return _fail("--confirm needs --confirmed-by NAME")
    if args.update and not args.changed_by:
        return _fail("--update needs --changed-by NAME")
    if (args.embed or args.advance) and settings.openai_api_key is None:
        return _fail(f"--{command} needs OPENAI_API_KEY")
    if args.delete_path and not (args.cleanup or args.advance):
        return _fail("--delete-path needs --cleanup or --advance")
    request = None
    if args.confirm or args.update:
        path = args.confirm or args.update
        try:
            raw = json.loads(Path(path).read_text("utf-8"))
            raw["company_id"] = args.company_id  # the command line wins over the file
            if args.confirm:
                raw["confirmed_by"] = args.confirmed_by
                request = ConfirmRequest.model_validate(raw)
            else:
                raw["changed_by"] = args.changed_by
                request = UpdateRequest.model_validate(raw)
        except (OSError, ValueError) as exc:
            return _fail(f"bad {command} file {path}: {exc}")
    try:
        schema = load_profile_schema(settings.profile_schema_path)
        repository = make_repository(settings)
    except (SchemaError, RepositoryError) as exc:
        return _fail(str(exc))
    loop_factory = asyncio.SelectorEventLoop if sys.platform == "win32" else None
    return asyncio.run(
        _steps(command, args, settings, schema, repository, request), loop_factory=loop_factory
    )


async def _steps(
    command: str,
    args: argparse.Namespace,
    settings: Settings,
    schema: ProfileSchema,
    repository: Repository,
    request: ConfirmRequest | UpdateRequest | None,
) -> int:
    company_id = args.company_id
    try:
        await repository.initialize()  # pipeline tables incl. project state, idempotent
        match command:
            case "review":
                view = await review_profile(repository, schema, settings, company_id)
                _print_review(view)
                _write_json(args.json, _review_payload(view))
            case "confirm":
                result = await confirm_profile(repository, schema, settings, request)
                _print_confirm(result)
                _write_json(args.json, {"confirm_result": result.model_dump(mode="json")})
            case "update":
                client = make_client(settings)
                updated = await update_profile(repository, schema, settings, request, client)
                _print_update(updated)
                _write_json(args.json, {"update_result": updated.model_dump(mode="json")})
            case "profile":
                profile = await read_profile(repository, schema, settings, company_id)
                _print_profile(profile)
                _write_json(args.json, {"profile": profile.model_dump(mode="json")})
            case "audit":
                audit_filter = AuditFilter(
                    company_id=company_id, kind=args.kind, acted_by=args.person
                )
                entries = await repository.list_audit(audit_filter)
                _print_audit(entries)
                _write_json(args.json, {"audit": [e.model_dump(mode="json") for e in entries]})
            case "embed":
                await _embed(repository, schema, settings, company_id)
            case "cleanup":
                await _cleanup(repository, settings, company_id, args.delete_path)
            case "advance":  # demo: embed, then clean up (also repairs stale vectors)
                await _embed(repository, schema, settings, company_id)
                await _cleanup(repository, settings, company_id, args.delete_path)
        return 0
    except DecisionError as exc:  # also UpdateError: every problem per field
        print(f"\n{command} refused, nothing changed:", file=sys.stderr)
        for line in str(exc).split("; "):
            print(f"  {line}", file=sys.stderr)
        return 1
    except (RepositoryError, EmbedError, CleanupError) as exc:
        print(f"\nerror: {exc}", file=sys.stderr)
        return 1
    finally:
        await repository.close()


SCREEN_COMMANDS = ("ingest_law", "laws", "screen", "screen_pending", "matches")


def _run_screening(args: argparse.Namespace, settings: Settings) -> int:
    """Steps 11 to 15: laws, screening and matches. Database only."""
    chosen = [c for c in SCREEN_COMMANDS if getattr(args, c)]
    if len(chosen) > 1:
        return _fail(f"choose one command at a time: {', '.join('--' + c for c in chosen)}")
    command = chosen[0]
    flag = "--" + command.replace("_", "-")
    if settings.repository != "postgres":
        return _fail(f"{flag} needs --repository postgres")
    if (args.replace or args.screen_after_ingest) and not args.ingest_law:
        return _fail("--replace and --screen-after-ingest need --ingest-law")
    if (args.dry_run or args.force or args.law_id is not None) and not args.screen:
        return _fail("--dry-run, --force and --law-id need --screen")
    if (args.screen_pending or args.matches) and not args.company_id:
        return _fail(f"{flag} needs --company-id")
    needs_key = args.ingest_law or args.screen_pending or (args.screen and not args.dry_run)
    if needs_key and settings.openai_api_key is None:
        return _fail(f"{flag} needs OPENAI_API_KEY")
    try:
        repository = make_repository(settings)
    except RepositoryError as exc:
        return _fail(str(exc))
    loop_factory = asyncio.SelectorEventLoop if sys.platform == "win32" else None
    return asyncio.run(_screening(command, args, settings, repository), loop_factory=loop_factory)


async def _screening(
    command: str, args: argparse.Namespace, settings: Settings, repository: Repository
) -> int:
    client = make_client(settings)
    screener = Screener(repository, settings, client)
    try:
        await repository.initialize()  # pipeline and screening tables, idempotent
        match command:
            case "ingest_law":
                report = await ingest_law(
                    Path(args.ingest_law), repository, settings, client, replace=args.replace
                )
                _print_ingest(report)
                _write_json(args.json, {"ingest": report.model_dump(mode="json")})
                if args.screen_after_ingest:
                    _print_screen(await screener.screen(law_id=report.law_id))
            case "laws":
                laws = await repository.list_laws()
                _print_laws(laws)
                _write_json(args.json, {"laws": [law.model_dump(mode="json") for law in laws]})
            case "screen":
                reports = await screener.screen(
                    args.company_id, args.law_id, dry_run=args.dry_run, force=args.force
                )
                _print_screen(reports)
                payload = [r.model_dump(mode="json", exclude={"scored"}) for r in reports]
                _write_json(args.json, {"screen": payload})
            case "screen_pending":
                _print_screen([await screener.screen_pending(args.company_id)])
            case "matches":
                matches = await repository.list_matches(args.company_id, args.only_hits)
                _print_matches(args.company_id, matches)
                _write_json(args.json, {"matches": [m.model_dump(mode="json") for m in matches]})
        return 0
    except (RepositoryError, IngestError, ScreenError, MatchError) as exc:
        print(f"\nerror: {exc}", file=sys.stderr)
        return 1
    finally:
        await repository.close()


def _print_ingest(report: IngestReport) -> None:
    replaced = " (replaced)" if report.replaced else ""
    print(f"\nIngested law {report.law_id}{replaced}: {report.title}")
    print(
        f"  kind {report.kind}, status {report.status}, published {report.published_at or '-'}, "
        f"effective {report.effective_at or '-'}"
    )
    print(f"  {report.sections} sections by {report.strategy}")
    if report.summary:
        print(f"  Zusammenfassung: {report.summary}")
    if report.change_summary:
        print(f"  Was ändert sich?: {report.change_summary}")
    print(f"  norms found: {', '.join(report.norms_found) or 'none'}")
    print(f"  norms not in the vocabulary: {', '.join(report.norms_unmatched) or 'none'}")
    for dropped in report.dropped:
        print(f"  dropped: {dropped}")
    print(f"{report.headline()}; tokens in={report.input_tokens} out={report.output_tokens}")


def _print_laws(laws: list[Law]) -> None:
    print(f"\n{'ID':>4}  {'STATUS':<13}{'SECTIONS':>9}  {'INGESTED':<17} TITLE")
    for law in laws:
        when = f"{law.ingested_at:%Y-%m-%d %H:%M}" if law.ingested_at else "-"
        print(f"{law.id:>4}  {law.status:<13}{law.sections:>9}  {when:<17} {law.title}")
        if law.summary:
            print(f"      Zusammenfassung: {law.summary}")
        if law.change_summary:
            print(f"      Was ändert sich?: {law.change_summary}")


def _print_screen(reports: list[ScreenReport]) -> None:
    for r in reports:
        if r.status != "screened":
            print(f"\n{r.company_id}: {r.status}: {r.message}")
            continue
        removed = ", ".join(f"{rule}={n}" for rule, n in r.removed.items())
        mode = " (dry run, nothing stored)" if r.dry_run else ""
        print(
            f"\nScreening '{r.company_id}'{mode}: {r.features} features x {r.sections} sections; "
            f"removed {removed}; pairs {r.pairs}"
        )
        if r.dry_run:
            print(f"  {'SCORE':>6} {'NORM':<5}{'PASSED':<7}{'SECTION':<28}BEST FEATURE")
            for s in r.scored:
                print(
                    f"  {s.score:>6.3f} {'yes' if s.norm_match else 'no':<5}"
                    f"{'yes' if s.passed else 'no':<7}{s.section.section_ref[:27]:<28}"
                    f"{s.features[0].feature.text_basis[:80]}"
                )
        else:
            print(
                f"  judged {r.judged}, skipped {r.skipped} (unchanged), hits {r.hits}; "
                f"tokens in={r.input_tokens} out={r.output_tokens}"
            )
            if r.unjudged:
                print(f"  unjudged (retried next screen): {', '.join(r.unjudged)}")
        print(f"  {r.headline()}")


def _print_matches(company_id: str, matches: list[Match]) -> None:
    hits = sum(m.judge_relevant for m in matches)
    print(f"\nMatches of '{company_id}': {len(matches)} stored, {hits} relevant")
    for m in matches:
        verdict = f"{m.judge_direction}" if m.judge_relevant else "not relevant"
        norm = ", norm match" if m.norm_match else ""
        print(f"  [{verdict}] {m.law_title}, {m.section_ref} (score {m.score:.3f}{norm})")
        print(f"      feature {m.feature_ref}: {m.feature_text}")
        print(f"      {m.judge_reason}")


async def _embed(
    repository: Repository, schema: ProfileSchema, settings: Settings, company_id: str
) -> None:
    client = make_client(settings)
    report = await Embedder(repository, schema, settings, client).run(company_id)
    print(f"\n{report.headline()} (model {report.model}, fallback {report.fallback})")
    for ref, sentence in report.sentences.items():
        marker = " [fallback]" if ref in report.fallback_features else ""
        print(f"  {ref}: {sentence}{marker}")


async def _cleanup(
    repository: Repository, settings: Settings, company_id: str, delete_paths: list[str] | None
) -> None:
    paths = [Path(p) for p in delete_paths or []]
    cleaned = await cleanup_project(repository, settings, company_id, paths)
    print(f"\nCleaned project '{company_id}': rows deleted {cleaned.rows_deleted}")
    print(f"  files deleted: {', '.join(cleaned.files_deleted) or 'none'}")
    print(f"  verified: rows left {cleaned.rows_left}, state {cleaned.state}")


def _print_confirm(result: ConfirmResult) -> None:
    print(f"\nConfirmed project '{result.company_id}' by {result.confirmed_by}:")
    print(
        f"  accepted={result.fields_accepted} conflicts_resolved={result.conflicts_resolved} "
        f"typed={result.values_typed} left_empty={result.fields_left_empty} "
        f"rows_dropped={result.rows_dropped}"
    )
    print(f"  hints closed={result.hints_closed} still open={result.hints_open}")


def _print_update(result: UpdateResult) -> None:
    print(
        f"\nUpdated project '{result.company_id}': {result.changes} change(s), "
        f"profile version {result.profile_version}, hints closed {result.hints_closed}"
    )
    if result.embeddings_refreshed:
        print(f"  vectors refreshed: {', '.join(result.changed_features) or 'metadata only'}")
    elif result.embeddings_stale:
        print(f"  vectors stale ({result.refresh_error}); run --advance to repair")


def _print_profile(profile: ConfirmedProfile) -> None:
    stale = ", vectors stale" if profile.embeddings_stale else ""
    print(
        f"\nProfile '{profile.company_id}' (state {profile.state}, "
        f"version {profile.profile_version}{stale})"
    )
    for name, value in profile.scalars.items():
        if value is not None:
            print(f"  {name:<24}{json.dumps(value, ensure_ascii=False)}")
    for name, rows in profile.lists.items():
        for row in rows:
            values = {k: v for k, v in row.values.items() if v is not None}
            print(f"  {name} #{row.id}: {json.dumps(values, ensure_ascii=False)}")
            print(f"      [{row.fundstelle or 'ohne Fundstelle'}]")
    print(f"Audit log: {len(profile.audit)} entr{'y' if len(profile.audit) == 1 else 'ies'}")


def _print_audit(entries: list[AuditEntry]) -> None:
    """Newest first: who, when, what (field, old and new value, reason)."""
    print(f"\nAudit log: {len(entries)} entr{'y' if len(entries) == 1 else 'ies'}")
    for e in entries:
        when = f"{e.acted_at:%Y-%m-%d %H:%M}" if e.acted_at else "-"
        if e.kind is AuditKind.PROFILE_CHANGED:
            where = f" #{e.row_id}" if e.row_id else ""
            what = (
                f"{e.change_kind} {e.field}{where} {json.dumps(e.old_value, ensure_ascii=False)}"
                f" -> {json.dumps(e.new_value, ensure_ascii=False)}"
            )
        else:
            what = json.dumps(e.new_value, ensure_ascii=False)
        reason = f" ({e.reason})" if e.reason else ""
        print(f"  v{e.profile_version} {when} {e.acted_by}: {e.kind} {what}{reason}")


def _review_payload(view: ReviewView) -> dict:
    """The review plus a decisions template: fill in `action` (one of `allowed`)."""
    template = {
        "company_id": view.company_id,
        "draft_version": view.draft_version,
        "decisions": [
            {
                "field": o.field,
                **({"key": o.key} if o.key is not None else {}),
                "action": None,
                "allowed": [str(a) for a in o.allowed],
                **({"cells": {c: {"group": None} for c in o.cells}} if o.cells else {}),
            }
            for o in view.open_decisions
        ],
    }
    return {"review": view.model_dump(mode="json"), "decisions_template": template}


def _print_review(view: ReviewView) -> None:
    print(
        f"\nReview of '{view.company_id}' (state {view.state or 'not published'}, "
        f"draft version {view.draft_version})"
    )
    print(f"Open decisions: {len(view.open_decisions)}")
    for o in view.open_decisions:
        field = view.draft.field(o.field)
        result = field
        if o.key is not None:
            result = next(i for i in field.items if i.key == o.key)
        where = f"{o.field} {json.dumps(o.key, ensure_ascii=False)}" if o.key else o.field
        cells = f" cells: {', '.join(o.cells)}" if o.cells else ""
        print(f"  {where}: {o.kind}{cells} -> {' | '.join(str(a) for a in o.allowed)}")
        for i, group in enumerate(result.groups):
            rank = f" rank={group.best_rank}" if group.best_rank is not None else ""
            sources = ", ".join(f"{c.source_filename} p.{c.source_page}" for c in group.candidates)
            value = json.dumps(group.value, ensure_ascii=False)
            print(f"      group {i}{rank}: {value} ({sources})")
    if view.needs_review:
        print(f"Needs review (no decision required): {', '.join(view.needs_review)}")
    print(f"Open pruefhinweis rows: {len(view.open_hints)}")
    for hint in view.open_hints:
        print(f"  [{hint.art}] {hint.feld}: {hint.beschreibung}")


async def _setup_db(repository: Repository, settings: Settings, args: argparse.Namespace) -> None:
    if not (args.init_db or args.seed or args.reset_profil):
        return
    await repository.initialize()
    applied = await repository.initialize_profile_schema(
        settings.profile_db_dir, reset=args.reset_profil, seed=args.seed
    )
    print(f"pipeline tables ready; profil files applied: {', '.join(applied) or 'none'}")


async def _publish(
    orchestrator: Orchestrator,
    draft: ProfileDraft,
    extractions: list[DocumentExtraction],
    *,
    run_scoped: bool = False,
) -> PublishReport | str:
    try:
        return await orchestrator.publish(draft, extractions, run_scoped=run_scoped)
    except (PublishError, StateError) as exc:  # StateError: the project is already confirmed
        return str(exc)


def _print_publish(published: PublishReport | str | None) -> int:
    """Exit code: 1 when publishing was requested and failed."""
    if published is None:
        return 0
    if isinstance(published, str):
        print(f"\nPublish failed: {published}", file=sys.stderr)
        return 1
    rows = ", ".join(f"{table}={n}" for table, n in sorted(published.rows.items()))
    print(f"\nPublished project '{published.project}' to schema profil: {rows}")
    print(f"  pruefhinweis rows: {published.hints}")
    for skipped in published.skipped:
        print(f"  skipped: {skipped}")
    return 0


def _print_report(documents: list[Document], summary: RunSummary) -> None:
    print(f"{'STATUS':<12}{'TYPE':<13}{'PAGES':>6}{'TIME':>9}  FILE")
    for doc in documents:
        print(
            f"{doc.status:<12}{doc.type:<13}{len(doc.pages):>6}"
            f"{doc.timings.total_s:>8.2f}s  {doc.relative_path}"
        )

    skipped = [doc for doc in documents if doc.status is DocStatus.UNSUPPORTED]
    if skipped:
        print("\nSkipped (unsupported):")
        for doc in skipped:
            print(f"  {doc.relative_path}: {'; '.join(doc.warnings)}")

    warnings = []
    for doc in documents:
        if doc.status is not DocStatus.UNSUPPORTED:  # already listed above
            warnings += [f"  {doc.relative_path}: {w}" for w in doc.warnings]
        for page in doc.pages:
            warnings += [f"  {doc.relative_path} [{page.label}]: {w}" for w in page.warnings]
    if warnings:
        print("\nWarnings:")
        print("\n".join(warnings))

    counts = ", ".join(f"{status}={count}" for status, count in sorted(summary.by_status.items()))
    tokens = summary.token_usage
    print(
        f"\n{summary.headline()} ({counts}); "
        f"tokens in={tokens.input_tokens} out={tokens.output_tokens}"
    )


def _print_extractions(run: ExtractionRun) -> None:
    extractions: list[DocumentExtraction] = sorted(run.extractions, key=lambda e: e.filename)
    print(
        f"\n{'EXTRACTION':<14}{'CHUNKS':>7}{'REPAIR':>8}{'FIELDS':>8}{'TOKENS':>9}{'TIME':>9}  FILE"
    )
    for e in extractions:
        tokens = e.token_usage.input_tokens + e.token_usage.output_tokens
        print(
            f"{e.status:<14}{e.chunks:>7}{'yes' if e.repair_used else 'no':>8}"
            f"{e.fields_found:>8}{tokens:>9}{e.agent_s:>8.2f}s  {e.filename}"
        )
    notes = [f"  {e.filename}: {w}" for e in extractions for w in e.warnings]
    if notes:
        print("\nExtraction warnings:")
        print("\n".join(notes))
    s = run.summary
    counts = ", ".join(f"{status}={n}" for status, n in sorted(s.by_status.items()))
    print(
        f"\n{s.headline()} ({counts}); candidates={s.candidates}; "
        f"tokens in={s.token_usage.input_tokens} out={s.token_usage.output_tokens}"
    )


def _print_draft(draft: ProfileDraft) -> None:
    counts = ", ".join(f"{status}={n}" for status, n in sorted(draft.counts.items()))
    print(f"\nProfile draft for {draft.company_id} ({counts})")
    for f in draft.fields:
        if f.items:
            print(f"  {f.name:<24}{f.status}: {len(f.items)} item(s){_flags(f)}")
            for item in f.items:
                _print_result(item, indent="      ", name=_key_text(item.key))
        else:
            _print_result(f, indent="  ", name=f.name)
    if draft.incomplete_documents:
        print("  Incomplete (not in this draft):")
        for d in draft.incomplete_documents:
            print(f"    {d.filename}: {d.status}: {d.reason}")
    if draft.db_incomplete:
        print("  WARNING: some candidates could not be written to the database")


def _print_result(f: FieldResult, *, indent: str, name: str) -> None:
    if f.status is FieldStatus.CONFLICT:
        print(f"{indent}{name:<24}CONFLICT{_flags(f)}")
        for i, group in enumerate(f.groups):
            marker = " (suggested)" if i == f.suggestion else ""
            rank = f" rank={group.best_rank}" if group.best_rank is not None else ""
            value = json.dumps(group.value, ensure_ascii=False)
            print(f"{indent}    group {i}{rank}: {value}{marker}")
            for c in group.candidates:
                print(
                    f"{indent}      {c.source_filename} p.{c.source_page} "
                    f"conf={c.confidence:.2f}: {c.evidence!r}"
                )
    elif f.status is FieldStatus.MISSING:
        print(f"{indent}{name:<24}missing")
    else:
        value = json.dumps(f.value, ensure_ascii=False)
        docs = len(f.groups[0].documents)
        print(f"{indent}{name:<24}{f.status}: {value} ({docs} document(s)){_flags(f)}")


def _flags(f: FieldResult) -> str:
    flags = [
        name
        for name, on in (
            ("needs review", f.needs_review),
            ("rounding-equivalent", f.rounding_equivalent),
            ("intra-document conflict", f.intra_document_conflict),
        )
        if on
    ]
    return f"  [{', '.join(flags)}]" if flags else ""


def _key_text(key: dict | None) -> str:
    return ", ".join(f"{v}" for v in (key or {}).values())


def _write_json(path: str | None, payload: dict) -> None:
    if path:
        Path(path).write_text(json.dumps(payload, indent=2, ensure_ascii=False), "utf-8")


def _fail(message: str) -> int:
    print(f"error: {message}", file=sys.stderr)
    return 2


def _parse_args(argv: list[str] | None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Parse a folder of DD documents.",
        epilog="Files written with --json or --markdown contain document text; step 10 "
        "(--cleanup) does not delete them.",
    )
    parser.add_argument("folder", nargs="?", help="folder with PDF and Excel files")
    parser.add_argument(
        "--json", metavar="FILE", help="write results as JSON (contains document text)"
    )
    parser.add_argument(
        "--markdown", action="store_true", help="print each document's markdown (document text)"
    )
    parser.add_argument("--log-level", default="INFO", help="logging level (default INFO)")

    extraction = parser.add_argument_group("extraction (steps 5 to 7)")
    extraction.add_argument("--extract", action="store_true", help="run agents and merge")
    extraction.add_argument(
        "--company-id", help="deal project name, profil.deal.projektname (default: folder name)"
    )
    extraction.add_argument("--schema", metavar="FILE", help="profile schema JSON file")
    extraction.add_argument("--repository", choices=["memory", "postgres"], help="DB to use")
    extraction.add_argument(
        "--merge-only", action="store_true", help="merge stored candidates of --company-id"
    )
    extraction.add_argument(
        "--publish", action="store_true", help="write the draft into schema profil (unconfirmed)"
    )

    database = parser.add_argument_group("database (postgres only)")
    database.add_argument(
        "--init-db", action="store_true", help="create pipeline tables and schema profil if missing"
    )
    database.add_argument("--seed", action="store_true", help="load the solution seeds")
    database.add_argument(
        "--reset-profil",
        action="store_true",
        help="drop and recreate schema profil (deletes every published profile)",
    )

    steps = parser.add_argument_group("confirm, embed, clean up (steps 8 to 10, postgres only)")
    steps.add_argument("--review", action="store_true", help="print open decisions of --company-id")
    steps.add_argument(
        "--confirm", metavar="FILE", help="apply a decisions file and confirm the profile"
    )
    steps.add_argument("--confirmed-by", metavar="NAME", help="advisor who confirms (--confirm)")
    steps.add_argument("--embed", action="store_true", help="one vector per profile feature")
    steps.add_argument(
        "--cleanup", action="store_true", help="delete working data once vectors exist"
    )
    steps.add_argument("--advance", action="store_true", help="--embed then --cleanup")
    steps.add_argument(
        "--delete-path",
        metavar="PATH",
        action="append",
        help="upload folder or file to delete in step 10 (must be inside DATA_DIR; repeatable)",
    )

    updates = parser.add_argument_group("after confirmation (plan section 4b, postgres only)")
    updates.add_argument("--profile", action="store_true", help="print the confirmed profile")
    updates.add_argument(
        "--update", metavar="FILE", help="apply a changes file to the confirmed profile"
    )
    updates.add_argument("--changed-by", metavar="NAME", help="advisor who changes (--update)")
    updates.add_argument(
        "--audit", action="store_true", help="print the audit log of --company-id, newest first"
    )
    updates.add_argument("--kind", choices=[str(k) for k in AuditKind], help="--audit filter")
    updates.add_argument("--person", metavar="NAME", help="--audit filter: acting person")

    screening = parser.add_argument_group("laws and screening (steps 11 to 15, postgres only)")
    screening.add_argument("--ingest-law", metavar="FILE", help="parse, split and embed a law PDF")
    screening.add_argument(
        "--replace", action="store_true", help="--ingest-law: replace the same stored file"
    )
    screening.add_argument(
        "--screen-after-ingest", action="store_true", help="--ingest-law: screen right after"
    )
    screening.add_argument("--laws", action="store_true", help="list the stored laws")
    screening.add_argument(
        "--screen", action="store_true", help="screen --company-id (or every company) vs laws"
    )
    screening.add_argument("--law-id", type=int, metavar="N", help="--screen: only this law")
    screening.add_argument(
        "--dry-run", action="store_true", help="--screen: stop before the judge, store nothing"
    )
    screening.add_argument("--force", action="store_true", help="--screen: re-judge everything")
    screening.add_argument(
        "--screen-pending", action="store_true", help="re-screen after advisor updates"
    )
    screening.add_argument("--matches", action="store_true", help="list matches of --company-id")
    screening.add_argument("--only-hits", action="store_true", help="--matches: relevant only")
    return parser.parse_args(argv)


if __name__ == "__main__":
    sys.exit(main())
