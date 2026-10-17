"""Parse a folder of DD documents and print results with timings.

Usage: uv run python -m app.cli <folder> [--json out.json] [--markdown] [--log-level INFO]
Exit code is non-zero only when the folder itself is unusable, not when single files fail.
"""

import argparse
import asyncio
import json
import logging
import sys
from pathlib import Path
from time import perf_counter

from app.ingest.pipeline import DocumentParser, RunSummary, summarize
from app.logging_setup import configure_logging
from app.models.document import DocStatus, Document

log = logging.getLogger("app.cli")


def main(argv: list[str] | None = None) -> int:
    args = _parse_args(argv)
    configure_logging(args.log_level)

    folder = Path(args.folder)
    if not folder.is_dir():
        print(f"error: not a folder: {folder}", file=sys.stderr)
        return 2

    documents, summary = asyncio.run(_run(folder))
    documents.sort(key=lambda doc: doc.relative_path)
    log.info(summary.headline())

    _print_report(documents, summary)
    if args.markdown:
        for doc in documents:
            print("\n" + doc.to_markdown())
    if args.json:
        payload = {
            "summary": summary.model_dump(mode="json"),
            "documents": [doc.model_dump(mode="json") for doc in documents],
        }
        Path(args.json).write_text(json.dumps(payload, indent=2, ensure_ascii=False), "utf-8")
    return 0


async def _run(folder: Path) -> tuple[list[Document], RunSummary]:
    parser = DocumentParser()
    start = perf_counter()
    documents = [doc async for doc in parser.parse_folder(folder)]
    return documents, summarize(documents, perf_counter() - start)


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


def _parse_args(argv: list[str] | None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Parse a folder of DD documents.")
    parser.add_argument("folder", help="folder with PDF and Excel files")
    parser.add_argument("--json", metavar="FILE", help="write documents and summary as JSON")
    parser.add_argument("--markdown", action="store_true", help="print each document's markdown")
    parser.add_argument("--log-level", default="INFO", help="logging level (default INFO)")
    return parser.parse_args(argv)


if __name__ == "__main__":
    sys.exit(main())
