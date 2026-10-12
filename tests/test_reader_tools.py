import pytest

from app.models.schemas import ReaderContentRecord, ReaderMutation, ReaderOperationRequest
from app.services.editor_session import EditorSessionService
from app.services.reader import ReaderNotFoundError, ReaderService, reader_edit_id


@pytest.fixture
def reading_tools(tmp_path):
    records = [
        ReaderContentRecord(
            book_id=book_id,
            title=f"书籍{book_id}",
            author="测试作者",
            chapter_index=chapter,
            chapter_title=f"章节{chapter}",
            paragraph_index=paragraph,
            text="共同关键词，原文相同。" if paragraph != 3 else "没有匹配文字。",
        )
        for book_id in ("a", "b")
        for chapter in (2, 0)
        for paragraph in (3, 1, 0)
    ]
    reader = ReaderService(
        reader_path=tmp_path / "reader.jsonl",
        books_path=tmp_path / "books.jsonl",
        chunks_path=tmp_path / "chunks.jsonl",
    )
    reader.replace_records(records)
    editor = EditorSessionService(reader)
    editor.annotation_path = tmp_path / "annotations.json"
    editor.bookmark_path = tmp_path / "bookmarks.json"
    editor.reset()
    return reader, editor, records


def test_search_pages_count_paragraphs_and_preserve_exact_positions(reading_tools):
    reader, _editor, records = reading_tools

    first = reader.search_page("a", "  关键词  ", offset=0, limit=3)
    second = reader.search_page("a", "关键词", offset=first.next_offset, limit=3)

    assert (first.total, first.offset, first.limit, first.next_offset) == (4, 0, 3, 3)
    assert second.total == 4
    assert second.next_offset is None
    items = first.items + second.items
    assert [(item.chapter_index, item.paragraph_index) for item in items] == [(0, 0), (0, 1), (2, 0), (2, 1)]
    assert len({item.edit_id for item in items}) == 4
    for item in items:
        source = next(record for record in records if reader_edit_id(record) == item.edit_id)
        assert source.book_id == "a"
        assert item.reader_location.chapter_index == source.chapter_index
        assert item.reader_location.start_paragraph_index == source.paragraph_index
        assert "关键词" in item.excerpt


def test_search_chapter_zero_empty_and_out_of_range_pages(reading_tools):
    reader, _editor, _records = reading_tools

    assert reader.search_page("a", "关键词", chapter_index=0).total == 2
    empty = reader.search_page("a", "不存在")
    assert empty.items == [] and empty.total == 0 and empty.next_offset is None
    past = reader.search_page("a", "关键词", offset=100)
    assert past.items == [] and past.total == 4 and past.next_offset is None
    with pytest.raises(ValueError):
        reader.search_page("a", "   ")
    with pytest.raises(ReaderNotFoundError):
        reader.search_page("missing", "关键词")


def test_search_and_identity_location_use_unsaved_edits_and_deletions(reading_tools):
    reader, editor, records = reading_tools
    target = next(item for item in records if item.book_id == "a" and item.chapter_index == 2 and item.paragraph_index == 1)
    edit_id = reader_edit_id(target)
    editor.apply_operation(ReaderOperationRequest(
        book_id="a", mutations=[ReaderMutation(edit_id=edit_id, text="尚未保存的独特关键词。")],
    ))

    page = reader.search_page("a", "独特关键词")
    assert page.total == 1 and page.items[0].edit_id == edit_id
    assert reader.locate_edit_id("a", edit_id).start_paragraph_index == 1
    assert reader.locate_edit_id("b", edit_id) is None

    editor.apply_operation(ReaderOperationRequest(
        book_id="a", mutations=[ReaderMutation(edit_id=edit_id, deleted=True)],
    ))
    assert reader.search_page("a", "独特关键词").total == 0
    assert reader.locate_edit_id("a", edit_id) is None
