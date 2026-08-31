from app.agent.citation_builder import build_citations, build_context
from app.models.schemas import RetrievedChunk, TextChunk


def make_retrieved_chunk() -> RetrievedChunk:
    chunk = TextChunk(
        chunk_id="book1:0:0",
        book_id="book1",
        title="Test Book",
        author="Test Author",
        chapter_index=0,
        chapter_title="Chapter 1",
        chunk_index=0,
        start_paragraph_index=0,
        end_paragraph_index=1,
        text="This is the source text.",
    )
    return RetrievedChunk(chunk=chunk, score=0.1)


def test_build_citations_contains_source_metadata():
    citations = build_citations([make_retrieved_chunk()])
    assert citations[0].title == "Test Book"
    assert citations[0].paragraph_range == "0-1"
    assert citations[0].source_id != "book1:0:0"
    assert citations[0].reader_location is not None
    assert citations[0].reader_location.book_id == "book1"
    assert citations[0].reader_location.chapter_index == 0
    assert citations[0].reader_location.start_paragraph_index == 0


def test_build_context_contains_text_and_number():
    context = build_context([make_retrieved_chunk()])
    assert "[1]" in context
    assert "This is the source text." in context
    assert "chunk_id" not in context
