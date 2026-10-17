"""Step 0 upload layer: path safety, streaming storage, limits and company ids."""

import pytest

from app.db.memory import MemoryRepository
from app.models.project import ProjectState, ProjectStatus
from app.runs.upload import (
    PARTIAL_DIR,
    IncomingFile,
    PathRejected,
    new_company,
    safe_relative_path,
    slugify,
    store_files,
)


def incoming(path: str, data: bytes = b"%PDF-1.4 data", fail_after: int | None = None):
    """A multipart part: reads `data` in small chunks; raises OSError after `fail_after`."""
    chunks = [data[i : i + 4] for i in range(0, len(data), 4)]
    calls = 0

    async def read(size: int) -> bytes:
        nonlocal calls
        calls += 1
        if fail_after is not None and calls > fail_after:
            raise OSError("connection lost")
        return chunks.pop(0) if chunks else b""

    return IncomingFile(relative_path=path, read=read)


@pytest.mark.parametrize(
    "path",
    [
        "../evil.pdf",
        "a/../../evil.pdf",
        "a\\..\\..\\evil.pdf",
        "/etc/passwd",
        "\\\\server\\share\\x.pdf",
        "C:/Windows/x.pdf",
        "c:evil.pdf",
        "a//b.pdf",
        "a/./b.pdf",
        "a/b/",
        "",
        "   ",
        "bad\x00name.pdf",
        "bad\nname.pdf",
        "x.pdf:stream",
        "a/CON.pdf",
        "folder./x.pdf",
        "name.pdf ",
        "a/" * 20 + "deep.pdf",
        "x" * 300 + ".pdf",
    ],
)
def test_unsafe_paths_are_rejected(path, settings):
    with pytest.raises(PathRejected):
        safe_relative_path(path, settings)


def test_safe_paths_are_normalized(settings):
    assert safe_relative_path("DD\\Steuern\\KSt 2022.pdf", settings).as_posix() == (
        "DD/Steuern/KSt 2022.pdf"
    )
    assert safe_relative_path("Bescheide/Umsätze.xlsx", settings).as_posix() == (
        "Bescheide/Umsätze.xlsx"
    )


async def test_store_keeps_paths_and_never_writes_outside(settings, tmp_path):
    folder = settings.data_dir / "uploads" / "c1"
    folder.mkdir(parents=True)
    files = [
        incoming("DD/Steuern/a.pdf"),
        incoming("DD/notes.txt", b"plain text"),
        incoming("../outside.pdf"),
        incoming("C:\\outside.pdf"),
        incoming("DD/steuern/A.pdf"),  # same path ignoring case
        incoming("DD/.DS_Store", b"junk"),
    ]

    accepted, rejected = await store_files(files, folder, settings)

    assert [a.relative_path for a in accepted] == ["DD/Steuern/a.pdf", "DD/notes.txt"]
    assert (folder / "DD" / "Steuern" / "a.pdf").read_bytes() == b"%PDF-1.4 data"
    reasons = {r.relative_path: r.reason for r in rejected}
    assert set(reasons) == {"../outside.pdf", "C:\\outside.pdf", "DD/steuern/A.pdf", "DD/.DS_Store"}
    assert "duplicate" in reasons["DD/steuern/A.pdf"]
    written = sorted(p.relative_to(tmp_path).as_posix() for p in tmp_path.rglob("*") if p.is_file())
    assert written == [
        "data/uploads/c1/DD/Steuern/a.pdf",
        "data/uploads/c1/DD/notes.txt",
    ]


async def test_limits_reject_single_files(settings):
    settings = settings.model_copy(
        update={"max_file_mb": 20 / (1024 * 1024), "upload_max_files": 2}
    )
    folder = settings.data_dir / "uploads" / "c2"
    folder.mkdir(parents=True)
    files = [
        incoming("big.pdf", b"x" * 50),
        incoming("small.pdf", b"y" * 10),
        incoming("second.pdf", b"z" * 10),
        incoming("third.pdf", b"w" * 10),
    ]

    accepted, rejected = await store_files(files, folder, settings)

    assert [a.relative_path for a in accepted] == ["small.pdf", "second.pdf"]
    reasons = {r.relative_path: r.reason for r in rejected}
    assert reasons["big.pdf"].startswith("file too large")
    assert reasons["third.pdf"].startswith("too many files")
    assert not (folder / "big.pdf").exists()


async def test_total_limit(settings):
    settings = settings.model_copy(update={"upload_max_total_mb": 25 / (1024 * 1024)})
    folder = settings.data_dir / "uploads" / "c3"
    folder.mkdir(parents=True)

    accepted, rejected = await store_files(
        [incoming("a.pdf", b"a" * 20), incoming("b.pdf", b"b" * 20)], folder, settings
    )

    assert [a.relative_path for a in accepted] == ["a.pdf"]
    assert rejected[0].reason.startswith("total upload too large")


async def test_broken_stream_leaves_no_half_file(settings):
    folder = settings.data_dir / "uploads" / "c4"
    folder.mkdir(parents=True)

    accepted, rejected = await store_files(
        [incoming("broken.pdf", b"0123456789abcdef", fail_after=2), incoming("ok.pdf")],
        folder,
        settings,
    )

    assert [a.relative_path for a in accepted] == ["ok.pdf"]
    assert rejected[0].reason == "could not store the file"
    assert not (folder / "broken.pdf").exists()
    assert not (folder / PARTIAL_DIR).exists()
    assert sorted(p.name for p in folder.iterdir()) == ["ok.pdf"]


def test_slugify():
    assert slugify("Hafen kante/Ost!") == "Hafen-kante-Ost"
    assert slugify("Übernahme Müller & Söhne") == "Ubernahme-Muller-Sohne"
    assert slugify("../..") == ""


async def test_company_ids_are_unique_and_never_seeded(settings):
    repository = MemoryRepository()
    repository.states["Nordhafen"] = ProjectStatus(
        company_id="Nordhafen", state=ProjectState.PUBLISHED
    )

    first, folder = await new_company(repository, settings, "Projekt Weser")
    second, _ = await new_company(repository, settings, "Projekt Weser")
    stored, _ = await new_company(repository, settings, "Nordhafen")
    seeded, _ = await new_company(repository, settings, "Hafenkante")  # the solution seed
    generated, _ = await new_company(repository, settings, None)

    assert first == "Projekt-Weser" and folder.is_dir()
    assert second.startswith("Projekt-Weser-") and second != first
    assert stored.startswith("Nordhafen-")
    assert seeded.startswith("Hafenkante-") and seeded.casefold() != "hafenkante"
    assert generated.startswith("dd-")
