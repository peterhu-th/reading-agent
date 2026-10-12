from pathlib import Path

import pytest
from ebooklib import epub

from app.ingestion.reader_epub import load_epub_for_reader
from app.models.reader_tools import AnnotationUpdateInput, BookmarkCreateInput
from app.models.schemas import AnnotationCreateRequest, ReaderContentRecord, ReaderMutation, ReaderOperationRequest
from app.services.editor_session import EditorSessionService
from app.services.epub_editor import EpubEditorService
from app.services.reader import ReaderService, reader_edit_id


def make_records():
    return [
        ReaderContentRecord(
            book_id=book_id, title=book_id, chapter_index=chapter, chapter_title=f"Chapter {chapter}",
            paragraph_index=position, text=f"paragraph {position} text",
        )
        for book_id in ("a", "b") for chapter in (0, 1) for position in range(3)
    ]


def open_editor(tmp_path, records=None):
    reader_path = tmp_path / "reader.jsonl"
    if records is not None:
        reader_path.write_text("".join(item.model_dump_json() + "\n" for item in records), encoding="utf-8")
    reader = ReaderService(reader_path=reader_path, books_path=tmp_path / "missing-books.jsonl", chunks_path=tmp_path / "missing-chunks.jsonl")
    editor = EditorSessionService(reader)
    editor.annotation_path = tmp_path / "annotations.json"
    editor.bookmark_path = tmp_path / "bookmarks.json"
    editor.epub_editor = EpubEditorService(tmp_path)
    editor.reset()
    return reader, editor


def add_bookmark(editor, record, label="bookmark"):
    bookmark, _state = editor.add_bookmark(record.book_id, BookmarkCreateInput(
        chapter_index=record.chapter_index, edit_id=reader_edit_id(record), label=label,
    ))
    return bookmark


def add_annotation(editor, record):
    annotation, _state = editor.add_annotation(AnnotationCreateRequest(
        book_id=record.book_id, chapter_index=record.chapter_index,
        start_edit_id=reader_edit_id(record), end_edit_id=reader_edit_id(record),
        end_offset=4, selected_text=record.text[:4], comment="original",
    ))
    return annotation


def mutate(editor, record, **changes):
    return editor.apply_operation(ReaderOperationRequest(
        book_id=record.book_id, mutations=[ReaderMutation(edit_id=reader_edit_id(record), **changes)],
    ))


def bookmark_ids(editor, book_id=None):
    return {item.bookmark_id for item in editor.bookmarks_snapshot(book_id)}


def make_epub(path):
    book = epub.EpubBook()
    book.set_identifier("bookmarks-test")
    book.set_title("Bookmarks test")
    book.set_language("en")
    chapters = []
    for number in (1, 2):
        chapter = epub.EpubHtml(uid=f"chapter{number}", title=f"Chapter {number}", file_name=f"chapter{number}.xhtml", lang="en")
        chapter.content = f"<html><body><h1>Chapter {number}</h1><p>chapter {number} body</p></body></html>"
        book.add_item(chapter)
        chapters.append(chapter)
    book.add_item(epub.EpubNcx())
    book.add_item(epub.EpubNav())
    book.toc = tuple(epub.Link(chapter.file_name, chapter.title, chapter.id) for chapter in chapters)
    book.spine = ["nav", *chapters]
    epub.write_epub(str(path), book)


def test_bookmark_create_delete_undo_save_and_restart_keep_other_book_cached(tmp_path):
    reader, editor = open_editor(tmp_path, make_records())
    first, second = reader.chapters[("a", 0)][:2]
    other_book = reader.get_book("b")
    first_note = add_bookmark(editor, first, "first")
    editor.undo()
    assert not editor.has_unsaved_changes
    assert not editor.bookmarks_snapshot()

    first_note = add_bookmark(editor, first, "first")
    second_note = add_bookmark(editor, second, "second")
    editor.save()
    assert reader.get_book("b") is other_book
    editor.delete_bookmark("a", first_note.bookmark_id)
    assert bookmark_ids(editor) == {second_note.bookmark_id}
    editor.undo()
    assert not editor.has_unsaved_changes
    assert bookmark_ids(editor) == {first_note.bookmark_id, second_note.bookmark_id}
    assert reader.get_book("b") is other_book

    _reader, restarted = open_editor(tmp_path)
    assert bookmark_ids(restarted, "a") == {first_note.bookmark_id, second_note.bookmark_id}
    assert not restarted.has_unsaved_changes
    restarted.delete_bookmark("a", first_note.bookmark_id)
    restarted.save()
    _reader, restarted = open_editor(tmp_path)
    assert bookmark_ids(restarted) == {second_note.bookmark_id}


def test_paragraph_and_chapter_deletion_remove_only_related_bookmarks_and_undo(tmp_path):
    reader, editor = open_editor(tmp_path, make_records())
    first, second = reader.chapters[("a", 0)][:2]
    another_chapter = reader.chapters[("a", 1)][0]
    another_book = reader.chapters[("b", 0)][0]
    notes = [add_bookmark(editor, record) for record in (first, second, another_chapter, another_book)]

    mutate(editor, first, text="changed text")
    assert bookmark_ids(editor) == {item.bookmark_id for item in notes}
    editor.undo()
    mutate(editor, first, text="")
    assert notes[0].bookmark_id not in bookmark_ids(editor)
    assert editor.resolve_location("a", reader_edit_id(first)) is None
    editor.undo()
    assert notes[0].bookmark_id in bookmark_ids(editor)

    editor.delete_chapter("a", 0)
    assert bookmark_ids(editor) == {notes[2].bookmark_id, notes[3].bookmark_id}
    editor.undo()
    assert bookmark_ids(editor) == {item.bookmark_id for item in notes}

    editor.apply_operation(ReaderOperationRequest(book_id="a", mutations=[
        ReaderMutation(edit_id=reader_edit_id(first), text="temporary edit"),
        ReaderMutation(edit_id=reader_edit_id(first), deleted=True),
    ]))
    editor.undo()
    assert bookmark_ids(editor) == {item.bookmark_id for item in notes}
    assert editor.reading_snapshot("a").records[0].text == first.text


def test_bookmark_save_migrates_chapter_and_edit_id_and_repeated_save(tmp_path):
    source = tmp_path / "book.epub"
    make_epub(source)
    reader, editor = open_editor(tmp_path, load_epub_for_reader(source))
    first = next(item for item in reader.records if item.text == "chapter 1 body")
    target = next(item for item in reader.records if item.text == "chapter 2 body")
    heading = next(item for item in reader.records if item.source_href == "chapter2.xhtml" and item.kind == "heading")
    deleted_bookmark = add_bookmark(editor, first)
    retained = add_bookmark(editor, target, "keep me")
    editor.save()
    editor.delete_chapter(first.book_id, first.chapter_index)
    mutate(editor, heading, deleted=True)
    editor.save()

    fresh = next(item for item in reader.records if item.text == target.text)
    bookmark = editor.bookmarks_snapshot()[0]
    assert bookmark.bookmark_id == retained.bookmark_id
    assert bookmark.chapter_index == fresh.chapter_index == target.chapter_index - 1
    assert bookmark.edit_id == reader_edit_id(fresh) != reader_edit_id(target)
    assert deleted_bookmark.bookmark_id not in bookmark_ids(editor)
    assert editor.resolve_location(fresh.book_id, bookmark.edit_id).start_paragraph_index == fresh.paragraph_index

    reader, editor = open_editor(tmp_path)
    fresh = next(item for item in reader.records if item.text == target.text)
    mutate(editor, fresh, text="changed after restart")
    editor.save()
    _reader, restarted = open_editor(tmp_path)
    bookmark = restarted.bookmarks_snapshot()[0]
    snapshot = restarted.reading_snapshot(bookmark.book_id)
    assert next(item.text for item in snapshot.records if reader_edit_id(item) == bookmark.edit_id) == "changed after restart"
    assert not snapshot.has_unsaved


@pytest.mark.parametrize("failure_phase", ["staging", "replacement"])
def test_bookmark_save_failure_keeps_all_files_and_pending_operations(tmp_path, monkeypatch, failure_phase):
    source = tmp_path / "book.epub"
    make_epub(source)
    reader, editor = open_editor(tmp_path, load_epub_for_reader(source))
    target = next(item for item in reader.records if item.text == "chapter 2 body")
    editor.save()
    originals = {path: path.read_bytes() for path in (source, reader.reader_path, editor.annotation_path, editor.bookmark_path)}
    note = add_bookmark(editor, target)
    add_annotation(editor, target)
    mutate(editor, target, text="changed body")
    state = editor.state()
    with monkeypatch.context() as patch:
        if failure_phase == "staging":
            def fail_write(_bookmarks):
                raise OSError("bookmark storage unavailable")
            patch.setattr(editor, "_write_bookmarks", fail_write)
        else:
            original_replace = Path.replace
            def fail_replace(path, destination):
                if Path(destination) == editor.bookmark_path:
                    raise PermissionError("bookmark replacement unavailable")
                return original_replace(path, destination)
            patch.setattr(Path, "replace", fail_replace)
        with pytest.raises(OSError):
            editor.save()

    assert editor.state() == state
    assert all(path.read_bytes() == contents for path, contents in originals.items())
    assert bookmark_ids(editor) == {note.bookmark_id}
    assert editor.reading_snapshot().has_unsaved
    assert not list(tmp_path.glob("*.tmp"))
    editor.undo()
    assert next(item.text for item in editor.reading_snapshot().records if reader_edit_id(item) == reader_edit_id(target)) == target.text
    editor.save()
    assert not editor.has_unsaved_changes


def test_reading_snapshot_is_an_independent_consistent_effective_view(tmp_path):
    reader, editor = open_editor(tmp_path, make_records())
    first, second = reader.chapters[("a", 0)][:2]
    bookmark = add_bookmark(editor, second)
    annotation = add_annotation(editor, second)
    editor.save()
    mutate(editor, first, deleted=True)
    mutate(editor, second, text="new paragraph")
    editor.rename_chapter("a", 0, "New chapter")
    snapshot = editor.reading_snapshot("a")
    assert snapshot.has_unsaved
    assert all(item.book_id == "a" for item in snapshot.records)
    assert reader_edit_id(first) not in {reader_edit_id(item) for item in snapshot.records}
    second_view = next(item for item in snapshot.records if reader_edit_id(item) == reader_edit_id(second))
    assert (second_view.text, second_view.chapter_title) == ("new paragraph", "New chapter")
    assert editor.resolve_location("a", reader_edit_id(second)).start_paragraph_index == second.paragraph_index
    second_view.text = "outside mutation"
    snapshot.annotations[0].comment = "outside mutation"
    snapshot.bookmarks[0].label = "outside mutation"
    assert next(item.text for item in editor.reading_snapshot("a").records if reader_edit_id(item) == reader_edit_id(second)) == "new paragraph"
    assert editor.annotations_snapshot("a")[0].comment == annotation.comment
    assert editor.bookmarks_snapshot("a")[0].label == bookmark.label


@pytest.mark.parametrize("case", ["cross_book", "cross_chapter", "wrong_chapter", "backwards", "backwards_offset", "start_offset", "end_offset", "deleted", "missing"])
def test_invalid_annotation_ranges_do_not_change_session(tmp_path, case):
    reader, editor = open_editor(tmp_path, make_records())
    first, second = reader.chapters[("a", 0)][:2]
    payload = dict(book_id="a", chapter_index=0, start_edit_id=reader_edit_id(first), end_edit_id=reader_edit_id(second), start_offset=0, end_offset=4)
    if case == "cross_book":
        payload["end_edit_id"] = reader_edit_id(reader.chapters[("b", 0)][0])
    elif case == "cross_chapter":
        payload["end_edit_id"] = reader_edit_id(reader.chapters[("a", 1)][0])
    elif case == "wrong_chapter":
        payload["chapter_index"] = 1
    elif case == "backwards":
        payload["start_edit_id"], payload["end_edit_id"] = payload["end_edit_id"], payload["start_edit_id"]
    elif case == "backwards_offset":
        payload.update(end_edit_id=reader_edit_id(first), start_offset=4, end_offset=1)
    elif case == "start_offset":
        payload["start_offset"] = len(first.text) + 1
    elif case == "end_offset":
        mutate(editor, second, text="x")
    elif case == "deleted":
        mutate(editor, first, deleted=True)
    elif case == "missing":
        payload["end_edit_id"] = "missing"
    state = editor.state()
    with pytest.raises(ValueError):
        editor.add_annotation(AnnotationCreateRequest(**payload))
    assert editor.state() == state
    assert not editor.annotations_snapshot()


def test_bookmark_location_and_book_scoping_are_checked_before_changes(tmp_path):
    reader, editor = open_editor(tmp_path, make_records())
    first = reader.chapters[("a", 0)][0]
    payload = BookmarkCreateInput(chapter_index=0, edit_id=reader_edit_id(first))
    with pytest.raises(ValueError):
        editor.add_bookmark("b", payload)
    with pytest.raises(ValueError):
        editor.add_bookmark("a", payload.model_copy(update={"chapter_index": 1}))
    note = add_bookmark(editor, first)
    state = editor.state()
    with pytest.raises(KeyError):
        editor.delete_bookmark("b", note.bookmark_id)
    assert editor.state() == state
    mutate(editor, first, deleted=True)
    with pytest.raises(ValueError):
        editor.add_bookmark("a", payload)


def test_annotation_patch_is_scoped_undoable_and_persisted(tmp_path):
    reader, editor = open_editor(tmp_path, make_records())
    record = reader.chapters[("a", 0)][0]
    annotation = add_annotation(editor, record)
    editor.save()
    with pytest.raises(KeyError):
        editor.update_annotation("b", annotation.annotation_id, AnnotationUpdateInput(comment="wrong book"))
    with pytest.raises(KeyError):
        editor.delete_annotation(annotation.annotation_id, book_id="b")
    assert not editor.has_unsaved_changes

    updated, state = editor.update_annotation("a", annotation.annotation_id, AnnotationUpdateInput(comment="updated", color="green"))
    assert (updated.comment, updated.color) == ("updated", "green")
    assert (updated.start_edit_id, updated.end_offset) == (annotation.start_edit_id, annotation.end_offset)
    _updated, same_state = editor.update_annotation("a", annotation.annotation_id, AnnotationUpdateInput(comment="updated"))
    assert same_state == state
    editor.undo()
    assert not editor.has_unsaved_changes
    assert editor.annotations_snapshot()[0] == annotation

    editor.update_annotation("a", annotation.annotation_id, AnnotationUpdateInput(comment="", color="blue"))
    editor.save()
    _reader, restarted = open_editor(tmp_path)
    restored = restarted.annotations_snapshot()[0]
    assert (restored.comment, restored.color) == ("", "blue")
    assert not restarted.has_unsaved_changes


def test_annotation_offsets_count_unicode_code_points_and_follow_text_edits(tmp_path):
    record = ReaderContentRecord(book_id="a", title="a", chapter_index=0, paragraph_index=0, text="A😀B")
    _reader, editor = open_editor(tmp_path, [record])
    edit_id = reader_edit_id(record)
    annotation, _state = editor.add_annotation(AnnotationCreateRequest(
        book_id="a", chapter_index=0, start_edit_id=edit_id, end_edit_id=edit_id,
        start_offset=1, end_offset=2, selected_text="😀",
    ))
    mutate(editor, record, text="XA😀B")
    updated = editor.annotations_snapshot()[0]
    assert (updated.start_offset, updated.end_offset, updated.selected_text) == (2, 3, "😀")
    editor.undo()
    assert editor.annotations_snapshot()[0] == annotation
    with pytest.raises(ValueError):
        editor.add_annotation(AnnotationCreateRequest(
            book_id="a", chapter_index=0, start_edit_id=edit_id, end_edit_id=edit_id,
            start_offset=1, end_offset=4,
        ))
