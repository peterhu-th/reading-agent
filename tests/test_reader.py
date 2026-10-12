import json
from pathlib import Path

import pytest

from app.ingestion.normalize import is_reader_visible_paragraph
from app.models.schemas import BookParagraph, ReaderContentRecord, TextChunk
from app.services.reader import ReaderNotFoundError, ReaderService


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


class CountingEditor:
    def __init__(self):
        self.calls = []
        self.replacements = {}
        self.deleted_chapters = set()

    def apply_records(self, records):
        self.calls.append((records[0].book_id, records[0].chapter_index))
        return [
            record.model_copy(update={"text": self.replacements.get(record.book_id, record.text)})
            for record in records
            if (record.book_id, record.chapter_index) not in self.deleted_chapters
        ]

    def annotations_for_record(self, record):
        return []


def cached_reader(tmp_path):
    records = [
        ReaderContentRecord(
            book_id=book_id,
            title=book_id,
            chapter_index=chapter_index,
            chapter_title=f"章节{chapter_index}",
            paragraph_index=paragraph_index,
            text=f"{book_id} 章节{chapter_index} 段落{paragraph_index}",
        )
        for book_id in ("a", "b")
        for chapter_index in (0, 2, 7)
        for paragraph_index in range(4)
    ]
    reader = ReaderService(reader_path=tmp_path / "reader.jsonl")
    reader.replace_records(records)
    editor = CountingEditor()
    reader.set_editor(editor)
    return reader, editor


def test_repeated_pages_reuse_metadata_and_only_apply_current_chapter(tmp_path):
    reader, editor = cached_reader(tmp_path)
    chapters = reader.list_chapters("a")
    reader.get_book("a")
    editor.calls.clear()

    assert reader.list_chapters("a") is chapters
    for offset in (0, 2):
        page = reader.get_chapter("a", 2, offset=offset, limit=2)
        assert page.previous_chapter_index == 0
        assert page.next_chapter_index == 7
        assert page.chapter is chapters[1]
    assert editor.calls == [("a", 2), ("a", 2)]


def test_edit_invalidates_only_target_book_metadata_and_updates_navigation(tmp_path):
    reader, editor = cached_reader(tmp_path)
    reader.books
    other_book = reader.get_book("b")
    other_chapters = reader.list_chapters("b")
    editor.calls.clear()
    editor.replacements["a"] = "修改后的预览"
    editor.deleted_chapters.add(("a", 2))
    reader.invalidate_effective({"a"})

    assert reader.get_book("b") is other_book
    assert reader.list_chapters("b") is other_chapters
    assert editor.calls == []
    assert reader.get_book("a").chapter_count == 2
    assert reader.get_book("a").paragraph_count == 8
    assert reader.list_chapters("a")[0].preview == "修改后的预览"
    assert reader.get_chapter("a", 0).next_chapter_index == 7
    assert all(book_id == "a" for book_id, _ in editor.calls)


def test_saved_snapshot_keeps_other_book_caches_and_handles_changed_book_id(tmp_path):
    reader, editor = cached_reader(tmp_path)
    reader.books
    other_book = reader.get_book("b")
    other_chapters = reader.list_chapters("b")
    other_records = reader.chapters[("b", 2)]
    saved = [
        record.model_copy(update={"book_id": "new-a", "chapter_index": 1})
        if record.book_id == "a" else record.model_copy()
        for record in reader.records
        if record.book_id != "a" or record.chapter_index == 7
    ]
    reader.replace_records(saved, affected_book_ids={"a", "new-a"})
    editor.calls.clear()

    assert reader.get_book("b") is other_book
    assert reader.list_chapters("b") is other_chapters
    assert editor.calls == []
    assert reader.chapters[("b", 2)] is other_records
    with pytest.raises(ReaderNotFoundError):
        reader.get_book("a")
    page = reader.get_chapter("new-a", 1)
    assert page.book.paragraph_count == 4
    assert page.previous_chapter_index is None
    assert page.next_chapter_index is None
    assert {book.book_id for book in reader.books} == {"new-a", "b"}
