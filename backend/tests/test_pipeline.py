import time

from app.ingest import pipeline
from app.ingest.pipeline import DocumentParser, summarize
from app.models.document import DocStatus, DocType
from tests.helpers import SENTENCE, FakeClient, make_pdf, make_simple_excel


async def collect(parser: DocumentParser, folder):
    return [doc async for doc in parser.parse_folder(folder)]


async def test_ocr_runs_in_parallel_under_limit(tmp_path, settings):
    for i in range(10):
        make_pdf(tmp_path / f"scan_{i}.pdf", [None])
    client = FakeClient(delay=1.0)
    limited = settings.model_copy(update={"ocr_concurrency": 5})

    start = time.perf_counter()
    docs = await collect(DocumentParser(limited, client), tmp_path)
    elapsed = time.perf_counter() - start

    assert len(docs) == 10 and all(d.type is DocType.SCANNED_PDF for d in docs)
    assert client.completions.max_in_flight == 5
    assert 1.9 < elapsed < 3.5, f"expected about 2 s (two waves of 5), got {elapsed:.2f}s"


async def test_documents_are_yielded_before_slow_one_finishes(tmp_path, settings, monkeypatch):
    make_pdf(tmp_path / "slow.pdf", [SENTENCE])
    make_pdf(tmp_path / "fast_1.pdf", [SENTENCE])
    make_pdf(tmp_path / "fast_2.pdf", [SENTENCE])
    make_simple_excel(tmp_path / "data.xlsx")

    real_parse = pipeline.parse_pdf_text

    def slow_parse(path, min_chars):
        if path.name == "slow.pdf":
            time.sleep(1.5)
        return real_parse(path, min_chars)

    monkeypatch.setattr(pipeline, "parse_pdf_text", slow_parse)

    start = time.perf_counter()
    yielded = [
        (doc.filename, time.perf_counter() - start)
        async for doc in DocumentParser(settings, FakeClient()).parse_folder(tmp_path)
    ]

    assert yielded[-1][0] == "slow.pdf"
    assert all(elapsed < 1.0 for _, elapsed in yielded[:-1])


async def test_failures_are_isolated(tmp_path, settings, monkeypatch):
    make_pdf(tmp_path / "good.pdf", [SENTENCE])
    make_pdf(tmp_path / "scan.pdf", [None])
    make_simple_excel(tmp_path / "crash.xlsx")
    (tmp_path / "corrupt.pdf").write_bytes(b"%PDF-1.4\nbroken")

    def crash(path, row_cap):
        raise RuntimeError("parser bug")

    monkeypatch.setattr(pipeline, "parse_excel", crash)
    client = FakeClient(errors=[ValueError("model exploded")])

    docs = {d.filename: d for d in await collect(DocumentParser(settings, client), tmp_path)}

    assert len(docs) == 4  # one result per discovered file
    assert docs["good.pdf"].status is DocStatus.OK
    assert docs["corrupt.pdf"].status is DocStatus.FAILED
    assert docs["crash.xlsx"].status is DocStatus.FAILED
    assert "unexpected error" in docs["crash.xlsx"].warnings[0]
    assert docs["scan.pdf"].status is DocStatus.FAILED
    assert "OCR failed" in docs["scan.pdf"].pages[0].warnings[0]


async def test_missing_api_key_only_affects_scanned_pages(tmp_path, settings):
    make_pdf(tmp_path / "text.pdf", [SENTENCE])
    make_pdf(tmp_path / "scan.pdf", [None])
    make_simple_excel(tmp_path / "data.xlsx")

    parser = DocumentParser(settings)  # settings fixture has no key, so no client
    docs = {d.filename: d for d in await collect(parser, tmp_path)}

    assert parser.client is None
    assert docs["text.pdf"].status is DocStatus.OK
    assert docs["data.xlsx"].status is DocStatus.OK
    scan_page = docs["scan.pdf"].pages[0]
    assert scan_page.needs_review and "OPENAI_API_KEY" in scan_page.warnings[0]


async def test_summary(tmp_path, settings):
    make_pdf(tmp_path / "a.pdf", [SENTENCE])
    (tmp_path / "notes.docx").write_text("x")

    docs = await collect(DocumentParser(settings), tmp_path)
    summary = summarize(docs, wall_s=1.23)

    assert summary.documents == 2
    assert summary.by_status == {DocStatus.OK: 1, DocStatus.UNSUPPORTED: 1}
    assert summary.headline() == "2 documents in 1.2 seconds"
