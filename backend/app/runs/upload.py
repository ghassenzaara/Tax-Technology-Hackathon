"""Step 0 upload layer: untrusted relative paths, streaming storage and new company ids.

Every relative path comes from the browser and is untrusted. A file is written only when its
normalized path is relative, has no empty, `.` or `..` segment, no control or Windows-special
characters, stays under the length and depth caps, and resolves inside the company folder.
Files stream to a hidden temporary name in chunks and move into place only when complete, so
a broken upload never leaves a half file that looks valid. Nothing is rejected by file type:
the parser reports unsupported files. A file over a limit is rejected on its own; the rest
of the upload continues.
"""

import asyncio
import contextlib
import os
import re
import shutil
import unicodedata
import uuid
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from datetime import date
from pathlib import Path, PurePosixPath
from secrets import token_hex

from pydantic import BaseModel

from app.config import Settings
from app.db.repository import Repository
from app.ingest.discover import is_junk

UPLOADS = "uploads"  # below data_dir; one folder per company id
PARTIAL_DIR = ".partial"  # hidden, so discovery never sees a half file
SEED_PROJECT = re.compile(r"INSERT INTO deal\s*\([^)]*\)\s*VALUES\s*\(\s*'([^']+)'", re.S)
WINDOWS_SPECIAL = set('<>:"|?*')  # ':' would also open an NTFS alternate data stream
WINDOWS_RESERVED = {"con", "prn", "aux", "nul"} | {
    f"{p}{n}" for p in ("com", "lpt") for n in "123456789"
}
ID_MAX_CHARS = 48


class AcceptedFile(BaseModel):
    relative_path: str
    size: int  # bytes


class RejectedFile(BaseModel):
    relative_path: str  # as sent by the browser
    reason: str


class PathRejected(ValueError):
    """The relative path is unsafe or invalid; the message is the reason for the UI."""


@dataclass
class IncomingFile:
    """One file of the multipart request: its relative path and an async chunk reader."""

    relative_path: str
    read: Callable[[int], Awaitable[bytes]]


def safe_relative_path(raw: str, settings: Settings) -> PurePosixPath:
    """The normalized relative path, or PathRejected with the reason."""
    if not raw or not raw.strip():
        raise PathRejected("empty path")
    if any(ord(c) < 32 or ord(c) == 127 for c in raw):
        raise PathRejected("path contains control characters")
    path = unicodedata.normalize("NFC", raw).replace("\\", "/")
    if path.startswith("/"):
        raise PathRejected("absolute path")
    if re.match(r"[A-Za-z]:", path):
        raise PathRejected("path with a drive letter")
    if len(path) > settings.upload_max_path_chars:
        raise PathRejected(f"path longer than {settings.upload_max_path_chars} characters")
    parts = path.split("/")
    if len(parts) > settings.upload_max_path_depth:
        raise PathRejected(f"path deeper than {settings.upload_max_path_depth} levels")
    for part in parts:
        if part in ("", ".", ".."):
            raise PathRejected("path with an empty, '.' or '..' segment")
        if WINDOWS_SPECIAL & set(part):
            raise PathRejected(f"path contains one of {' '.join(sorted(WINDOWS_SPECIAL))}")
        if part.endswith((" ", ".")):
            raise PathRejected("path segment ends with a space or a dot")
        if part.split(".")[0].lower() in WINDOWS_RESERVED:
            raise PathRejected(f"reserved file name '{part}'")
    return PurePosixPath(*parts)


async def store_files(
    files: list[IncomingFile], folder: Path, settings: Settings
) -> tuple[list[AcceptedFile], list[RejectedFile]]:
    """Write the files below `folder`; never outside it. Returns accepted and rejected."""
    root = folder.resolve()
    partial = root / PARTIAL_DIR
    partial.mkdir(parents=True, exist_ok=True)
    max_file = int(settings.max_file_mb * 1024 * 1024)
    budget = int(settings.upload_max_total_mb * 1024 * 1024)
    accepted: list[AcceptedFile] = []
    rejected: list[RejectedFile] = []
    seen: set[str] = set()
    try:
        for incoming in files:
            shown = incoming.relative_path

            def reject(reason: str, shown: str = shown) -> None:
                rejected.append(RejectedFile(relative_path=shown, reason=reason))

            try:
                relative = safe_relative_path(incoming.relative_path, settings)
            except PathRejected as exc:
                reject(str(exc))
                continue
            target = (root / relative).resolve()
            if target == root or not target.is_relative_to(root) or target.is_relative_to(partial):
                reject("path resolves outside the upload folder")
                continue
            key = relative.as_posix().casefold()  # Windows and macOS ignore case
            if key in seen:
                reject("duplicate path in this upload")
                continue
            if is_junk(Path(relative)):
                reject("hidden or system file, ignored")
                continue
            if len(accepted) >= settings.upload_max_files:
                reject(f"too many files (limit {settings.upload_max_files})")
                continue
            seen.add(key)
            size, problem = await _stream(
                incoming, partial, target, min(max_file, budget), settings.upload_chunk_bytes
            )
            if problem:
                if size > max_file:
                    problem = f"file too large (limit {settings.max_file_mb} MB)"
                elif size > budget:
                    problem = f"total upload too large (limit {settings.upload_max_total_mb} MB)"
                reject(problem)
                continue
            budget -= size
            accepted.append(AcceptedFile(relative_path=relative.as_posix(), size=size))
    finally:
        shutil.rmtree(partial, ignore_errors=True)
    return accepted, rejected


async def _stream(
    incoming: IncomingFile, partial: Path, target: Path, limit: int, chunk_bytes: int
) -> tuple[int, str | None]:
    """Copy in chunks to a temporary name, then move into place. (bytes, problem)."""
    temp = partial / f"{uuid.uuid4().hex}.part"
    size = 0
    try:
        with temp.open("wb") as handle:
            while chunk := await incoming.read(chunk_bytes):
                size += len(chunk)
                if size > limit:
                    return size, "over the limit"
                await asyncio.to_thread(handle.write, chunk)
        await asyncio.to_thread(_move, temp, target)
        return size, None
    except OSError:
        return size, "could not store the file"
    finally:
        with contextlib.suppress(OSError):
            temp.unlink(missing_ok=True)


def _move(temp: Path, target: Path) -> None:
    target.parent.mkdir(parents=True, exist_ok=True)
    os.replace(temp, target)


def upload_root(settings: Settings) -> Path:
    return settings.data_dir / UPLOADS


def slugify(name: str) -> str:
    """ASCII letters, digits, '-' and '_' only ('Hafenkante Ost' -> 'Hafenkante-Ost')."""
    ascii_name = unicodedata.normalize("NFKD", name).encode("ascii", "ignore").decode()
    slug = re.sub(r"[^A-Za-z0-9_-]+", "-", ascii_name).strip("-_")
    return slug[:ID_MAX_CHARS].rstrip("-_")


def seeded_projects(sql_dir: Path) -> set[str]:
    """Project names the solution seeds insert (casefolded); never handed out."""
    names = set()
    for path in sorted(Path(sql_dir).glob("1[0-9]_seed_*.sql")):
        names |= {m.casefold() for m in SEED_PROJECT.findall(path.read_text("utf-8"))}
    return names


async def new_company(
    repository: Repository, settings: Settings, project_name: str | None
) -> tuple[str, Path]:
    """A company id never used before and its freshly created upload folder.

    Derived from the project name (slugged) or generated from the date and a short random
    part. On collision with a stored project, a deal in `profil`, a seeded project or an
    existing folder a short random suffix is appended. Creating the folder reserves the id,
    so two concurrent uploads never get the same one.
    """
    base = slugify(project_name or "") or f"dd-{date.today():%Y%m%d}-{token_hex(3)}"
    reserved = seeded_projects(settings.profile_db_dir)
    root = upload_root(settings)
    root.mkdir(parents=True, exist_ok=True)
    candidate = base
    for _ in range(20):
        if candidate.casefold() not in reserved and not await repository.company_exists(candidate):
            folder = root / candidate
            try:
                folder.mkdir()
            except FileExistsError:
                pass
            else:
                return candidate, folder
        candidate = f"{base[: ID_MAX_CHARS - 5]}-{token_hex(2)}"
    raise RuntimeError("could not find a free company id")
