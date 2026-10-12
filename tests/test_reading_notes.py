import json

import pytest

from app.models.reader_tools import BookmarkCreateInput, ReaderBookmark
from app.models.schemas import (
    AnnotationCreateRequest,
    ReaderAnnotation,
    ReaderContentRecord,
    ReaderMutation,
    ReaderOperationRequest,
)
from app.services.editor_session import EditorSessionService
from app.services.reader import ReaderNotFoundError, ReaderService, reader_edit_id
from app.services.reading_notes import ReadingNotesService


@pytest.fixture
def notes_environment(tmp_path):
    records = [
        ReaderContentRecord(
            book_id=book_id, title=f"书籍{book_id}", author="测试作者",
            chapter_index=chapter, chapter_title=f"章节{chapter}",
            paragraph_index=paragraph, text="重复的原文内容。",
        )
        for book_id in ("a", "b")
        for chapter in (0, 2)
        for paragraph in range(3)
    ]
    reader = ReaderService(
        reader_path=tmp_path / "reader.jsonl", books_path=tmp_path / "books.jsonl",
        chunks_path=tmp_path / "chunks.jsonl",
    )
    reader.replace_records(records)
    editor = EditorSessionService(reader)
    editor.annotation_path = tmp_path / "annotations.json"
    editor.bookmark_path = tmp_path / "bookmarks.json"
    editor.reset()
    return ReadingNotesService(reader, editor), reader, editor, records


def add_annotation(editor, start, end=None, **changes):
    end = end or start
    return editor.add_annotation(AnnotationCreateRequest(
        book_id=start.book_id, chapter_index=start.chapter_index,
        start_edit_id=reader_edit_id(start), end_edit_id=reader_edit_id(end),
        start_offset=0, end_offset=len(end.text),
        selected_text="最初保存的引文", comment="阅读后的评注", color="green", **changes,
    ))[0]


def add_bookmark(editor, record, label="继续阅读"):
    return editor.add_bookmark(record.book_id, BookmarkCreateInput(
        chapter_index=record.chapter_index, edit_id=reader_edit_id(record), label=label,
    ))[0]


def test_note_pages_filter_book_and_chapter_zero_and_locate_duplicate_text(notes_environment):
    notes, _reader, editor, records = notes_environment
    annotations = [add_annotation(editor, record) for record in records]
    bookmarks = [add_bookmark(editor, record) for record in records]

    page = notes.list_annotations("a", limit=2, chapter_index=0)
    assert (page.total, page.offset, page.limit, page.next_offset) == (3, 0, 2, 2)
    assert [item.reader_location.start_paragraph_index for item in page.items] == [0, 1]
    assert all(item.book_id == "a" and item.chapter_index == 0 for item in page.items)
    tail = notes.list_annotations("a", offset=page.next_offset, limit=2, chapter_index=0)
    assert len(tail.items) == 1 and tail.next_offset is None
    assert notes.annotation_location("a", annotations[1].annotation_id).start_paragraph_index == 1
    bookmark_page = notes.list_bookmarks("a", limit=2, chapter_index=0)
    assert (bookmark_page.total, bookmark_page.next_offset) == (3, 2)
    assert notes.bookmark_location("a", bookmarks[1].bookmark_id).start_paragraph_index == 1
    assert notes.list_annotations("a", offset=100).items == []
    with pytest.raises(KeyError):
        notes.annotation_location("b", annotations[1].annotation_id)
    with pytest.raises(KeyError):
        notes.bookmark_location("b", bookmarks[1].bookmark_id)
    with pytest.raises(ReaderNotFoundError):
        notes.list_bookmarks("a", chapter_index=99)
    with pytest.raises(ReaderNotFoundError):
        notes.export_snapshot("unknown")


def test_multiline_quote_and_exports_reflect_unsaved_text_and_chapter_title(notes_environment):
    notes, _reader, editor, records = notes_environment
    first, middle, last = records[:3]
    annotation = add_annotation(editor, first, last)
    bookmark = add_bookmark(editor, middle)
    edited_text = "尚未保存的正文。" * 40
    editor.apply_operation(ReaderOperationRequest(
        book_id="a", mutations=[ReaderMutation(edit_id=reader_edit_id(middle), text=edited_text)],
    ))
    editor.rename_chapter("a", 0, "修改后的章节")

    page = notes.list_annotations("a")
    view = page.items[0]
    assert view.annotation_id == annotation.annotation_id
    assert view.selected_text == "最初保存的引文"
    assert view.quote == "\n".join([first.text, edited_text, last.text])
    assert view.chapter_title == "修改后的章节"
    assert view.reader_location.end_paragraph_index == 2
    assert len(view.excerpt) < len(view.quote)
    exported = notes.export_snapshot("a")
    payload = json.loads(exported.model_dump_json())
    assert payload["book"]["title"] == "书籍a"
    assert payload["book"]["author"] == "测试作者"
    assert payload["has_unsaved_changes"]
    assert payload["annotations"][0]["color"] == "green"
    assert payload["annotations"][0]["comment"] == "阅读后的评注"
    assert payload["bookmarks"][0]["bookmark_id"] == bookmark.bookmark_id
    assert payload["bookmarks"][0]["quote"] == edited_text
    assert "source_epub" not in exported.model_dump_json()
    markdown = notes.render_markdown(exported)
    assert edited_text in markdown
    assert "编辑会话存在未保存修改：是" in markdown
    assert "修改后的章节" in markdown
    assert "阅读后的评注" in markdown and "颜色：green" in markdown
    assert annotation.created_at in markdown and annotation.updated_at in markdown
    assert not editor.annotation_path.exists()
    assert not editor.bookmark_path.exists()


def test_stale_note_ids_stay_exportable_without_matching_another_identical_paragraph(notes_environment):
    notes, _reader, editor, records = notes_environment
    record = records[0]
    annotation = ReaderAnnotation(
        book_id="a", chapter_index=0, start_edit_id="missing-start", end_edit_id="missing-end",
        selected_text=record.text, end_offset=len(record.text), comment="保留的旧评注",
    )
    bookmark = ReaderBookmark(book_id="a", chapter_index=0, edit_id="missing", label="保留的旧书签")
    editor.annotation_path.write_text(json.dumps([annotation.model_dump()], ensure_ascii=False), encoding="utf-8")
    editor.bookmark_path.write_text(json.dumps([bookmark.model_dump()], ensure_ascii=False), encoding="utf-8")
    editor.reset()

    view = notes.list_annotations("a").items[0]
    assert view.reader_location is None and view.quote == record.text
    assert notes.list_bookmarks("a").items[0].reader_location is None
    assert notes.annotation_location("a", annotation.annotation_id) is None
    assert notes.bookmark_location("a", bookmark.bookmark_id) is None
    snapshot = notes.export_snapshot("a")
    assert not snapshot.has_unsaved_changes
    assert len(snapshot.annotations) == len(snapshot.bookmarks) == 1
    markdown = notes.render_markdown(snapshot)
    assert "原文位置已失效" in markdown
    assert "保留的旧评注" in markdown and "保留的旧书签" in markdown


def test_cross_chapter_saved_annotation_has_no_location(notes_environment):
    notes, _reader, editor, records = notes_environment
    start, end = records[0], records[3]
    annotation = ReaderAnnotation(
        book_id="a", chapter_index=0, start_edit_id=reader_edit_id(start), end_edit_id=reader_edit_id(end),
        selected_text="旧引文", end_offset=len(end.text),
    )
    editor.annotation_path.write_text(json.dumps([annotation.model_dump()], ensure_ascii=False), encoding="utf-8")
    editor.reset()

    view = notes.list_annotations("a").items[0]
    assert view.reader_location is None
    assert view.quote == "旧引文"
