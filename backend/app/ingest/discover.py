"""Walk an upload folder, drop OS/editor junk, classify each file by extension and content.

Text vs scan is not decided here: PDFs can be mixed, so the PDF parser decides per page.
"""

from dataclasses import dataclass
from enum import StrEnum
from pathlib import Path

from app.config import Settings


class FileFormat(StrEnum):
    PDF = "pdf"
    EXCEL = "excel"
    UNSUPPORTED = "unsupported"


# TODO: decide on legacy .xls and standalone images (would need OCR).
SUPPORTED_EXTENSIONS = {
    ".pdf": FileFormat.PDF,
    ".xlsx": FileFormat.EXCEL,
    ".xlsm": FileFormat.EXCEL,
}

JUNK_NAMES = {"thumbs.db", "desktop.ini", ".ds_store"}


@dataclass(frozen=True)
class DiscoveredFile:
    path: Path
    relative_path: str  # posix style, relative to the upload folder
    file_format: FileFormat
    problem: str | None = None  # set when the file cannot be parsed; always set for UNSUPPORTED


def discover(root: Path, settings: Settings) -> list[DiscoveredFile]:
    """All non-junk files below `root`, sorted by path for deterministic runs."""
    files = []
    for path in sorted(root.rglob("*")):
        if path.is_file() and not is_junk(path.relative_to(root)):
            files.append(classify(path, root, settings))
    return files


def classify(path: Path, root: Path, settings: Settings) -> DiscoveredFile:
    relative = path.relative_to(root).as_posix()
    file_format = SUPPORTED_EXTENSIONS.get(path.suffix.lower(), FileFormat.UNSUPPORTED)
    size = path.stat().st_size

    def found(fmt: FileFormat, problem: str | None = None) -> DiscoveredFile:
        return DiscoveredFile(path, relative, fmt, problem)

    if size == 0:
        return found(FileFormat.UNSUPPORTED, "empty file (0 bytes)")
    if file_format is FileFormat.UNSUPPORTED:
        return found(file_format, f"unsupported file type '{path.suffix or '(none)'}'")
    if size > settings.max_file_mb * 1024 * 1024:
        return found(
            file_format, f"file too large ({size / 1e6:.1f} MB, cap {settings.max_file_mb} MB)"
        )
    if not _content_matches(path, file_format):
        return found(file_format, f"content is not a valid {file_format} file")
    return found(file_format)


def is_junk(relative: Path) -> bool:
    name = relative.name
    hidden = any(part.startswith(".") for part in relative.parts)
    return hidden or name.startswith("~$") or name.lower() in JUNK_NAMES


def _content_matches(path: Path, file_format: FileFormat) -> bool:
    with path.open("rb") as handle:
        head = handle.read(1024)
    if file_format is FileFormat.PDF:
        return b"%PDF-" in head  # the spec allows leading bytes before the header
    return head.startswith(b"PK\x03\x04")  # xlsx/xlsm are zip archives
