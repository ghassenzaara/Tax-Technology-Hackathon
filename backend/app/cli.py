"""Parse a folder of DD documents, optionally extract and merge a profile draft.

Usage:
  uv run python -m app.cli <folder> [--json out.json] [--markdown] [--log-level INFO]
  uv run python -m app.cli <folder> --extract [--company-id PROJECT] [--schema FILE]
                           [--repository memory|postgres] [--publish] [--json out.json]
  uv run python -m app.cli --merge-only --company-id PROJECT [--publish]
  uv run python -m app.cli --init-db [--seed] [--reset-profil] --repository postgres

The company id is the deal's project name (profil.deal.projektname). --publish writes the
draft into schema profil as unconfirmed rows, replacing an earlier publish of that project.

Exit code is non-zero only when the run cannot start (bad folder, bad schema file, missing
key or database settings), not when single files fail.
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
    make_repository,
)
from app.ingest.pipeline import DocumentParser, RunSummary, summarize
from app.logging_setup import configure_logging
from app.models.document import DocStatus, Document
from app.models.extraction import DocumentExtraction
from app.models.profile import FieldResult, FieldStatus, ProfileDraft
from app.profile.schema import ProfileSchema, SchemaError, load_profile_schema

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
    except PublishError as exc:
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
            print(f"{indent}    option {i + 1}{rank}: {value}{marker}")
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
    parser = argparse.ArgumentParser(description="Parse a folder of DD documents.")
    parser.add_argument("folder", nargs="?", help="folder with PDF and Excel files")
    parser.add_argument("--json", metavar="FILE", help="write results as JSON")
    parser.add_argument("--markdown", action="store_true", help="print each document's markdown")
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
    return parser.parse_args(argv)


if __name__ == "__main__":
    sys.exit(main())
