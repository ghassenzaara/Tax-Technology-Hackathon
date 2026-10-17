from app.agents.chunking import chunk_document
from tests.helpers import make_document


def chunk_settings(settings, **overrides):
    # 1 char per token makes sizes easy to reason about.
    values = {"chars_per_token": 1.0, "chunk_trigger_tokens": 500, "chunk_target_tokens": 300}
    return settings.model_copy(update=values | overrides)


def test_short_document_is_one_chunk(settings):
    doc = make_document(pages=["kurz"] * 3)

    chunks, warnings = chunk_document(doc, chunk_settings(settings))

    assert len(chunks) == 1 and warnings == []
    assert chunks[0].markdown == doc.to_markdown()


def test_long_document_splits_on_pages_with_overlap(settings):
    doc = make_document(pages=[f"Seite {n} " + "x" * 100 for n in range(1, 9)])

    chunks, warnings = chunk_document(doc, chunk_settings(settings, chunk_overlap_pages=1))

    pages = [[p.page_no for p in c.pages] for c in chunks]
    assert len(chunks) > 1 and warnings == []
    assert pages[0][0] == 1 and pages[-1][-1] == 8
    for previous, current in zip(pages, pages[1:], strict=False):
        assert current[0] == previous[-1]  # one overlap page repeated
    for chunk in chunks:  # original page markers kept
        for page in chunk.pages:
            assert f"## Page {page.page_no}\n" in chunk.markdown
        assert [c.index for c in chunks] == list(range(len(chunks)))


def test_without_overlap_every_page_once(settings):
    doc = make_document(pages=["x" * 100 for _ in range(8)])

    chunks, _ = chunk_document(doc, chunk_settings(settings, chunk_overlap_pages=0))

    assert [p.page_no for c in chunks for p in c.pages] == list(range(1, 9))


def test_oversized_page_sent_alone_with_warning(settings):
    doc = make_document(pages=["a" * 50, "b" * 1000, "c" * 50])

    chunks, warnings = chunk_document(doc, chunk_settings(settings))

    assert [[p.page_no for p in c.pages] for c in chunks] == [[1], [2], [3]]
    assert len(warnings) == 1 and "page 2" in warnings[0]
