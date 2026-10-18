"""Step 10 service: delete the working data of a company once its vectors exist.

Only from state embedded and only when the stored vectors equal the expected set: deleting
earlier would lose the only source of the features. Deletes the company's candidates,
extraction records and drafts (they hold file names, evidence quotes and every candidate)
plus the upload folders (given paths, the folders the runs record and the company's
deterministic upload folder), which must lie inside the configured data directory. Nothing
in `profil` is deleted. Ends with a verification of the actual rows and files; on any
failure the state stays embedded, the folder references stay, and the step is safe to
repeat. A second run on a cleaned project only deletes leftover files.

Known limits (not handled here): files the user writes with the CLI (`--json`,
`--markdown`) contain document text; backups, write-ahead logs and the OpenAI side are
out of scope (deferred with security).
"""

import asyncio
import logging
import shutil
from pathlib import Path
from time import perf_counter

from pydantic import BaseModel

from app.config import Settings
from app.db.repository import Repository, StateError
from app.models.project import ProjectState
from app.runs.upload import UPLOADS

log = logging.getLogger(__name__)


class CleanupError(RuntimeError):
    """Cleanup was refused or did not finish; the state stays embedded."""


class CleanupReport(BaseModel):
    company_id: str
    state: ProjectState
    rows_deleted: dict[str, int] = {}
    rows_left: dict[str, int] = {}
    files_deleted: list[str] = []
    files_left: list[str] = []
    seconds: float = 0.0

    @property
    def ok(self) -> bool:
        return not any(self.rows_left.values()) and not self.files_left


def checked_paths(paths: list[Path], settings: Settings) -> list[Path]:
    """Resolve the paths; refuse all of them if one lies outside the data directory."""
    root = settings.data_dir.resolve()
    resolved = [Path(p).resolve() for p in paths]
    outside = [str(p) for p in resolved if p == root or not p.is_relative_to(root)]
    if outside:
        raise CleanupError(
            f"refused: paths outside the data directory {root}: {', '.join(outside)}"
        )
    return resolved


def upload_folder(settings: Settings, company_id: str) -> Path | None:
    """The API's deterministic upload folder of a company (None if the id would escape it)."""
    root = (settings.data_dir / UPLOADS).resolve()
    folder = (root / company_id).resolve()
    return folder if folder != root and folder.is_relative_to(root) else None


async def cleanup_targets(
    repository: Repository, settings: Settings, company_id: str, paths: list[Path] = ()
) -> list[Path]:
    """Explicit paths plus the upload folders the runs still record plus the deterministic
    upload folder (which also covers projects whose folder reference an older version
    cleared too early). All must lie inside the data directory."""
    stored = [Path(r.folder) for r in await repository.list_runs(company_id) if r.folder]
    default = upload_folder(settings, company_id)
    targets = checked_paths([*paths, *stored, *([default] if default else [])], settings)
    return list(dict.fromkeys(targets))


async def cleanup_project(
    repository: Repository,
    settings: Settings,
    company_id: str,
    paths: list[Path] = (),
) -> CleanupReport:
    """Delete working rows and upload files of a company, then verify. The runs keep their
    folder reference until the files are verified gone; `mark_cleaned` then clears it with
    the state change. Already deleted paths count as deleted, so a retry is idempotent and
    always checks the actual files."""
    start = perf_counter()
    targets = await cleanup_targets(repository, settings, company_id, list(paths))
    state = await repository.get_project_state(company_id)
    runs = await repository.list_runs(company_id)
    if (
        runs
        and runs[0].started_by == "update_dd"
        and await repository.get_profile_draft(company_id)
    ):
        raise StateError("new documents await advisor review before cleanup")
    if state is not None and state.state is ProjectState.CLEANED:
        report = await _verify(repository, company_id, targets, ProjectState.CLEANED)
        if report.ok:  # repeated run: nothing left to do
            return report
        # Leftover files of a cleaned project (an older cleanup lost its folder): delete.
    elif state is None or state.state is not ProjectState.EMBEDDED:
        current = state.state if state else "not published"
        raise StateError(f"project '{company_id}' is {current}: cleanup needs state embedded")
    else:
        stored = await repository.list_embedding_keys(company_id, state.embed_model or "")
        expected = set(state.expected_features)
        if state.embeddings_written != state.embeddings_expected or stored != expected:
            raise CleanupError(
                f"refused: embedding set incomplete for '{company_id}' "
                f"(expected {len(expected)}, stored {len(stored)}); run --embed again"
            )

    await repository.begin_step(company_id, "cleanup", settings.step_lock_timeout_s)
    try:
        try:
            deleted = await repository.delete_working_data(company_id)
            files = await asyncio.to_thread(_delete_files, targets)
        except Exception as exc:
            await repository.record_error(company_id, f"cleanup failed: {exc}")
            raise CleanupError(f"cleanup failed for '{company_id}': {exc}") from exc

        report = await _verify(repository, company_id, targets, state.state)
        report.rows_deleted, report.files_deleted = deleted, files
        if not report.ok:
            left = [f"{t}={n}" for t, n in report.rows_left.items() if n] + report.files_left
            await repository.record_error(company_id, f"cleanup incomplete: {', '.join(left)}")
            raise CleanupError(f"cleanup incomplete for '{company_id}': {', '.join(left)}")
        await repository.mark_cleaned(company_id)  # clears the folder references too
    finally:
        await repository.end_step(company_id)
    report.state = ProjectState.CLEANED
    report.seconds = perf_counter() - start
    log.info(
        "cleaned project=%s rows=%s files=%d in %.1f seconds",
        company_id,
        deleted,
        len(files),
        report.seconds,
    )
    return report


def _delete_files(targets: list[Path]) -> list[str]:
    """Delete what exists; a path that is already gone counts as deleted."""
    deleted = []
    for path in targets:
        if path.is_dir():
            shutil.rmtree(path)
        elif path.exists():
            path.unlink()
        else:
            continue
        deleted.append(str(path))
    return deleted


async def _verify(
    repository: Repository, company_id: str, targets: list[Path], state: ProjectState
) -> CleanupReport:
    """Zero working rows and no file left?"""
    rows = await repository.count_working_rows(company_id)
    return CleanupReport(
        company_id=company_id,
        state=state,
        rows_left=rows,
        files_left=[str(p) for p in targets if p.exists()],
    )
