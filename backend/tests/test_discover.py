from app.ingest.discover import FileFormat, discover
from tests.helpers import SENTENCE, make_pdf, make_simple_excel


def test_classifies_files_and_skips_junk(tmp_path, settings):
    make_pdf(tmp_path / "a.pdf", [SENTENCE])
    make_pdf(tmp_path / "sub" / "c.pdf", [SENTENCE])
    make_simple_excel(tmp_path / "b.xlsx")
    (tmp_path / "fake.pdf").write_text("not a pdf")
    (tmp_path / "empty.pdf").write_bytes(b"")
    (tmp_path / "notes.docx").write_text("x")
    # Junk: lock file, hidden file, OS files, hidden folder.
    (tmp_path / "~$b.xlsx").write_text("lock")
    (tmp_path / ".hidden.pdf").write_text("x")
    (tmp_path / "Thumbs.db").write_text("x")
    make_pdf(tmp_path / ".git" / "x.pdf", [SENTENCE])

    files = {f.relative_path: f for f in discover(tmp_path, settings)}

    assert list(files) == sorted(files)
    assert set(files) == {"a.pdf", "b.xlsx", "empty.pdf", "fake.pdf", "notes.docx", "sub/c.pdf"}
    assert files["a.pdf"].file_format is FileFormat.PDF and files["a.pdf"].problem is None
    assert files["sub/c.pdf"].problem is None
    assert files["b.xlsx"].file_format is FileFormat.EXCEL and files["b.xlsx"].problem is None
    assert files["fake.pdf"].file_format is FileFormat.PDF
    assert "not a valid pdf" in files["fake.pdf"].problem
    assert files["empty.pdf"].file_format is FileFormat.UNSUPPORTED
    assert "empty" in files["empty.pdf"].problem
    assert files["notes.docx"].file_format is FileFormat.UNSUPPORTED


def test_file_size_cap(tmp_path, settings):
    make_pdf(tmp_path / "a.pdf", [SENTENCE])
    tiny_cap = settings.model_copy(update={"max_file_mb": 0.0001})

    [found] = discover(tmp_path, tiny_cap)

    assert "too large" in found.problem
