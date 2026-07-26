from app.ingestion.chunking import chunk_paragraphs
from app.models.schemas import BookParagraph


def make_paragraph(index: int, text: str, chapter_index: int = 0) -> BookParagraph:
    return BookParagraph(
        book_id="book1",
        title="Test Book",
        author="Test Author",
        source_path="test.epub",
        chapter_index=chapter_index,
        chapter_title="Chapter 1",
        paragraph_index=index,
        text=text,
    )


def test_chunk_paragraphs_empty():
    assert chunk_paragraphs([]) == []


def test_chunk_paragraphs_creates_chunks():
    paragraphs = [make_paragraph(i, "text " * 50) for i in range(5)]
    chunks = chunk_paragraphs(paragraphs, chunk_size=100)
    assert len(chunks) > 0
    assert chunks[0].chunk_id
    assert chunks[0].text
    assert chunks[0].title == "Test Book"


def test_chunk_paragraphs_uses_overlap():
    paragraphs = [make_paragraph(i, f"paragraph-{i} " * 5) for i in range(6)]
    chunks = chunk_paragraphs(paragraphs, chunk_size=90, overlap=40)

    assert len(chunks) > 1
    assert chunks[0].end_paragraph_index >= chunks[1].start_paragraph_index
    assert "paragraph-0" in chunks[0].text
    assert "paragraph-0" in chunks[1].text


def test_chunk_paragraphs_does_not_cross_chapters():
    paragraphs = [
        make_paragraph(0, "chapter zero text " * 10, chapter_index=0),
        make_paragraph(0, "chapter one text " * 10, chapter_index=1),
    ]
    chunks = chunk_paragraphs(paragraphs, chunk_size=1000)
    assert {chunk.chapter_index for chunk in chunks} == {0, 1}


def test_chunk_ids_are_stable_for_same_input():
    paragraphs = [make_paragraph(i, "stable text " * 8) for i in range(3)]

    first = chunk_paragraphs(paragraphs, chunk_size=120, overlap=30)
    second = chunk_paragraphs(paragraphs, chunk_size=120, overlap=30)

    assert [chunk.chunk_id for chunk in first] == [chunk.chunk_id for chunk in second]
