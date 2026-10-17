from pathlib import Path

from app.ingest.discover import DiscoveredFile, FileFormat
from app.ingest.normalize import build_document, clean_text, compute_doc_id
from app.models.document import DocStatus, DocType, Page, PageKind, Table, table_to_markdown


def test_markdown_contract():
    table = Table(rows=[["a", "b"], ["1", "2|3"]])
    file = DiscoveredFile(Path("x/report.pdf"), "x/report.pdf", FileFormat.PDF)
    pages = [
        Page(page_no=2, kind=PageKind.OCR, text="Scan", needs_review=True),
        Page(page_no=1, kind=PageKind.TEXT, text="Hallo  \n\n\n\nWelt", tables=[table]),
    ]

    doc = build_document(file, "id", pages=pages)

    assert doc.type is DocType.MIXED_PDF and doc.status is DocStatus.OK
    assert doc.to_markdown() == (
        "# Document: report.pdf\n\n"
        "## Page 1\n\n"
        "Hallo\n\nWelt\n\n"
        "| a | b |\n| --- | --- |\n| 1 | 2\\|3 |\n\n"
        "## Page 2 (OCR, needs review)\n\n"
        "Scan\n"
    )


def test_sheet_table_is_rendered_once():
    table = Table(rows=[["Kennzahl", "Wert"], ["Mitarbeiter", "25"]])
    file = DiscoveredFile(Path("b.xlsx"), "b.xlsx", FileFormat.EXCEL)
    page = Page(
        page_no=1,
        kind=PageKind.SHEET,
        sheet_name="Daten",
        text=table_to_markdown(table),
        tables=[table],
    )

    markdown = build_document(file, "id", pages=[page]).to_markdown()

    assert "## Sheet 1: Daten" in markdown
    assert markdown.count("| Kennzahl | Wert |") == 1


def test_status_rules():
    file = DiscoveredFile(Path("a.pdf"), "a.pdf", FileFormat.PDF)
    good = Page(page_no=1, kind=PageKind.TEXT, text="x")
    warned = Page(page_no=2, kind=PageKind.OCR, warnings=["OCR failed"])

    assert build_document(file, "id", pages=[good, warned]).status is DocStatus.PARTIAL
    assert build_document(file, "id", pages=[warned]).status is DocStatus.FAILED
    assert build_document(file, "id", error="boom").status is DocStatus.FAILED
    unsupported = DiscoveredFile(Path("a.docx"), "a.docx", FileFormat.UNSUPPORTED, "unsupported")
    assert build_document(unsupported, "id", error="unsupported").status is DocStatus.UNSUPPORTED


def test_clean_text_only_touches_whitespace():
    assert clean_text("  a  \n\n\n\nb\t \n") == "a\n\nb"


def test_doc_id_stable_and_content_sensitive(tmp_path):
    (tmp_path / "one").mkdir()
    (tmp_path / "two").mkdir()
    first = tmp_path / "one" / "bilanz.pdf"
    second = tmp_path / "two" / "bilanz.pdf"
    first.write_bytes(b"content A")
    second.write_bytes(b"content B")

    assert compute_doc_id(first, "one/bilanz.pdf") == compute_doc_id(first, "one/bilanz.pdf")
    assert compute_doc_id(first, "one/bilanz.pdf") != compute_doc_id(second, "two/bilanz.pdf")
