import json
from pathlib import Path

from app.ingestion.normalize import is_reader_visible_paragraph
from app.models.schemas import BookParagraph, TextChunk
from app.services.reader import ReaderService


def write_jsonl(path: Path, rows: list[BookParagraph | TextChunk]) -> None:
    path.write_text(
        "".join(json.dumps(row.model_dump(mode="json"), ensure_ascii=False) + "\n" for row in rows),
        encoding="utf-8",
    )


def paragraph(index: int, text: str, chapter: int = 0) -> BookParagraph:
    return BookParagraph(
        book_id="book",
        title="测试书",
        author="测试作者",
        source_path="D:/private/test.epub",
        chapter_index=chapter,
        chapter_title=f"第{chapter + 1}章",
        paragraph_index=index,
        text=text,
    )


def build_reader(tmp_path: Path) -> ReaderService:
    books_path = tmp_path / "books.jsonl"
    chunks_path = tmp_path / "chunks.jsonl"
    rows = [paragraph(index, f"这是第{index}段正文，包含足够的中文内容用于阅读器测试。") for index in range(12)]
    rows.append(paragraph(12, "https://example.com"))
    write_jsonl(books_path, rows)
    write_jsonl(
        chunks_path,
        [
            TextChunk(
                chunk_id="book:0:0",
                book_id="book",
                title="测试书",
                author="测试作者",
                book_type="fiction",
                chapter_index=0,
                chapter_title="第1章",
                chunk_index=0,
                start_paragraph_index=0,
                end_paragraph_index=11,
                text="正文",
            )
        ],
    )
    return ReaderService(reader_path=tmp_path / "missing-reader.jsonl", books_path=books_path, chunks_path=chunks_path)


def test_reader_filter_rejects_publication_noise():
    assert not is_reader_visible_paragraph("https://example.com/book")
    assert not is_reader_visible_paragraph("中国版本图书馆CIP数据核字第123号")
    assert not is_reader_visible_paragraph("开本：787mm×1092mm")
    assert is_reader_visible_paragraph("这是一段应当在阅读器中正常显示的正文内容。")


def test_reader_groups_and_pages_without_exposing_source_path(tmp_path):
    reader = build_reader(tmp_path)
    assert reader.books[0].chapter_count == 1
    assert reader.list_chapters("book")[0].paragraph_count == 13

    page = reader.get_chapter("book", 0, offset=0, limit=5)
    assert len(page.paragraphs) == 5
    assert page.next_offset == 5
    assert "source_path" not in page.model_dump()


def test_reader_focuses_and_searches_inside_book(tmp_path):
    reader = build_reader(tmp_path)
    page = reader.get_chapter("book", 0, limit=5, focus_paragraph=9)
    indexes = [item.paragraph_index for item in page.paragraphs]
    assert 9 in indexes

    hits = reader.search("book", "第9段", chapter_index=0)
    assert hits[0].paragraph_index == 9
    assert "source_path" not in hits[0].model_dump()


def test_reader_reload_discards_cached_snapshot(tmp_path):
    reader = build_reader(tmp_path)
    assert reader.books[0].paragraph_count == 13

    books_path = tmp_path / "books.jsonl"
    rows = [paragraph(index, f"更新后的第{index}段正文。") for index in range(2)]
    write_jsonl(books_path, rows)

    assert reader.books[0].paragraph_count == 13
    reader.reload()
    assert reader.books[0].paragraph_count == 2
