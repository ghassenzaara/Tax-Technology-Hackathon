from app.ingest.pdf_text import parse_pdf_text
from app.ingest.pipeline import DocumentParser
from app.models.document import DocStatus, DocType, PageKind
from tests.helpers import SENTENCE, FakeClient, make_pdf


def test_text_pdf_pages(tmp_path):
    path = make_pdf(tmp_path / "text.pdf", [SENTENCE, SENTENCE + "\nZweite Zeile"])

    result = parse_pdf_text(path, min_chars=50)

    assert result.error is None and result.needs_ocr == []
    assert [page.page_no for page in result.pages] == [1, 2]
    assert "Muster GmbH" in result.pages[0].text


def test_low_text_page_goes_to_ocr(tmp_path):
    path = make_pdf(tmp_path / "mixed.pdf", [SENTENCE, None])

    assert parse_pdf_text(path, min_chars=50).needs_ocr == [2]


async def test_mixed_pdf_routes_scan_page_to_ocr(tmp_path, settings):
    client = FakeClient()
    path = make_pdf(tmp_path / "mixed.pdf", [SENTENCE, None])

    doc = await DocumentParser(settings, client).parse_file(path)

    assert client.completions.calls == 1
    assert doc.type is DocType.MIXED_PDF and doc.status is DocStatus.OK
    text_page, ocr_page = doc.pages
    assert text_page.kind is PageKind.TEXT and not text_page.needs_review
    assert ocr_page.kind is PageKind.OCR and ocr_page.needs_review
    assert "Gescannter Inhalt" in ocr_page.text
    assert doc.token_usage.input_tokens == 100


async def test_corrupt_pdf_fails_cleanly(tmp_path, settings):
    path = tmp_path / "corrupt.pdf"
    path.write_bytes(b"%PDF-1.4\nthis is not a real pdf body")

    doc = await DocumentParser(settings, FakeClient()).parse_file(path)

    assert doc.status is DocStatus.FAILED and doc.warnings
