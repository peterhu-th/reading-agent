import json

import pytest
from ebooklib import epub

from app.ingestion.reader_epub import load_epub_for_reader
from app.models.schemas import AnnotationCreateRequest, ReaderContentRecord, ReaderMutation, ReaderOperationRequest
from app.services.editor_session import EditorSessionService
from app.services.epub_editor import EpubEditorService
from app.services.reader import ReaderService, reader_edit_id


def make_records():
    return [
        ReaderContentRecord(
            book_id=book_id, title=book_id, chapter_index=chapter, chapter_title=f"chapter {chapter}",
            paragraph_index=position, text=f"paragraph {position} text",
        )
        for book_id in ("a", "b") for chapter in (0, 1) for position in range(4)
    ]


def write_records(path, records):
    path.write_text("".join(json.dumps(item.model_dump(mode="json")) + "\n" for item in records), encoding="utf-8")


def open_editor(tmp_path, records=None):
    reader_path = tmp_path / "reader.jsonl"
    if records is not None:
        write_records(reader_path, records)
    reader = ReaderService(reader_path=reader_path)
    editor = EditorSessionService(reader)
    editor.annotation_path = tmp_path / "annotations.json"
    editor.bookmark_path = tmp_path / "bookmarks.json"
    editor.epub_editor = EpubEditorService(tmp_path)
    editor.reset()
    return reader, editor


def add_annotation(editor, start, end=None, start_offset=0, end_offset=4):
    end = end or start
    annotation, _state = editor.add_annotation(AnnotationCreateRequest(
        book_id=start.book_id, chapter_index=start.chapter_index,
        start_edit_id=reader_edit_id(start), end_edit_id=reader_edit_id(end),
        start_offset=start_offset, end_offset=end_offset,
    ))
    return annotation


def ids(editor, record):
    return {annotation.annotation_id for annotation in editor.annotations_for_record(record)}


def mutate(editor, record, **changes):
    return editor.apply_operation(ReaderOperationRequest(
        book_id=record.book_id, mutations=[ReaderMutation(edit_id=reader_edit_id(record), **changes)],
    ))


def test_annotation_ranges_are_isolated_by_book_and_chapter_and_support_undo(tmp_path):
    reader, editor = open_editor(tmp_path, make_records())
    first, second, third, fourth = reader.chapters[("a", 0)]
    other_chapter = reader.chapters[("a", 1)][0]
    other_book = reader.chapters[("b", 0)][0]
    span = add_annotation(editor, second, fourth)
    overlap = add_annotation(editor, third)
    chapter_note = add_annotation(editor, other_chapter)
    book_note = add_annotation(editor, other_book)

    assert ids(editor, first) == set()
    assert ids(editor, second) == ids(editor, fourth) == {span.annotation_id}
    assert ids(editor, third) == {span.annotation_id, overlap.annotation_id}
    assert ids(editor, other_chapter) == {chapter_note.annotation_id}
    assert ids(editor, other_book) == {book_note.annotation_id}

    editor.delete_annotation(span.annotation_id)
    assert ids(editor, second) == ids(editor, fourth) == set()
    assert ids(editor, third) == {overlap.annotation_id}
    editor.undo()
    assert ids(editor, third) == {span.annotation_id, overlap.annotation_id}
    editor.undo()
    assert ids(editor, other_book) == set()
    assert ids(editor, other_chapter) == {chapter_note.annotation_id}


def test_annotation_query_does_not_iterate_library_annotations(tmp_path):
    reader, editor = open_editor(tmp_path, make_records())
    target = reader.chapters[("a", 0)][1]
    note = add_annotation(editor, target)
    for record in reader.chapters[("b", 0)] + reader.chapters[("b", 1)]:
        add_annotation(editor, record)

    class CountScannedAnnotations(dict):
        scanned = 0

        def __iter__(self):
            self.scanned += len(self)
            return super().__iter__()

        def values(self):
            self.scanned += len(self)
            return super().values()

    annotations = CountScannedAnnotations(editor._annotations)
    editor._annotations = annotations
    page = reader.get_chapter("a", 0)
    assert [item.annotation_id for item in page.paragraphs[1].annotations] == [note.annotation_id]
    assert annotations.scanned == 0


def test_batch_endpoint_edits_and_paragraph_deletion_keep_index_consistent(tmp_path):
    reader, editor = open_editor(tmp_path, make_records())
    first, second, third, fourth = reader.chapters[("a", 0)]
    annotation = add_annotation(editor, second, fourth, start_offset=3, end_offset=5)
    editor.apply_operation(ReaderOperationRequest(book_id="a", mutations=[
        ReaderMutation(edit_id=reader_edit_id(second), text="X" + second.text),
        ReaderMutation(edit_id=reader_edit_id(fourth), text="YY" + fourth.text),
    ]))
    updated = editor.annotations_for_record(third)[0]
    assert (updated.start_offset, updated.end_offset) == (4, 7)
    editor.undo()
    restored = editor.annotations_for_record(third)[0]
    assert (restored.start_offset, restored.end_offset) == (3, 5)

    mutate(editor, first, deleted=True)
    page = reader.get_chapter("a", 0)
    assert all(item.annotations[0].annotation_id == annotation.annotation_id for item in page.paragraphs)
    mutate(editor, third, text="")
    assert ids(editor, second) == ids(editor, fourth) == set()
    editor.undo()
    assert ids(editor, second) == ids(editor, third) == ids(editor, fourth) == {annotation.annotation_id}


def test_chapter_delete_and_undo_leave_other_book_cached_and_annotated(tmp_path):
    reader, editor = open_editor(tmp_path, make_records())
    target = reader.chapters[("a", 0)][0]
    untouched = reader.chapters[("b", 0)][0]
    annotation = add_annotation(editor, target)
    other_annotation = add_annotation(editor, untouched)
    other_book = reader.get_book("b")
    other_chapters = reader.list_chapters("b")
    editor.delete_chapter("a", 0)
    assert ids(editor, target) == set()
    assert ids(editor, untouched) == {other_annotation.annotation_id}
    assert reader.get_book("b") is other_book
    assert reader.list_chapters("b") is other_chapters
    editor.undo()
    assert ids(editor, target) == {annotation.annotation_id}
    assert reader.get_book("b") is other_book
    assert reader.list_chapters("b") is other_chapters


def test_annotation_only_save_delete_undo_and_reload_keep_index_consistent(tmp_path):
    reader, editor = open_editor(tmp_path, make_records())
    first, second = reader.chapters[("a", 0)][:2]
    annotation = add_annotation(editor, first)
    other = add_annotation(editor, second)
    book = reader.get_book("a")
    chapters = reader.list_chapters("a")
    editor.save()
    assert reader.get_book("a") is book
    assert reader.list_chapters("a") is chapters
    editor.delete_annotation(annotation.annotation_id)
    editor.undo()
    assert not editor.has_unsaved_changes
    assert ids(editor, first) == {annotation.annotation_id}
    assert ids(editor, second) == {other.annotation_id}

    editor.delete_annotation(annotation.annotation_id)
    reader.reload()
    editor.reset()
    first, second = reader.chapters[("a", 0)][:2]
    assert ids(editor, first) == {annotation.annotation_id}
    assert ids(editor, second) == {other.annotation_id}
    assert not editor.has_unsaved_changes


def test_epub_save_reindexes_annotations_and_reuses_other_book_records(tmp_path):
    source = tmp_path / "a.epub"
    book = epub.EpubBook()
    book.set_identifier("a-epub")
    book.set_title("a")
    book.set_language("en")
    chapter = epub.EpubHtml(uid="chapter", title="Chapter", file_name="chapter.xhtml", lang="en")
    chapter.content = "<html><body><h1>Chapter</h1><p>alpha text</p><p>beta text</p></body></html>"
    book.add_item(chapter)
    book.add_item(epub.EpubNcx())
    book.add_item(epub.EpubNav())
    book.toc = (epub.Link("chapter.xhtml", "Chapter", "chapter"),)
    book.spine = ["nav", chapter]
    epub.write_epub(str(source), book)
    records = load_epub_for_reader(source) + [item for item in make_records() if item.book_id == "b"]
    reader, editor = open_editor(tmp_path, records)
    heading = next(item for item in reader.records if item.text == "Chapter" and item.kind == "heading")
    target = next(item for item in reader.records if item.text == "beta text")
    untouched = reader.chapters[("b", 0)][0]
    annotation = add_annotation(editor, target)
    other_annotation = add_annotation(editor, untouched)
    other_book = reader.get_book("b")
    other_chapters = reader.list_chapters("b")
    mutate(editor, heading, deleted=True)
    editor.save()

    fresh_target = next(item for item in reader.records if item.text == "beta text")
    assert reader_edit_id(fresh_target) != reader_edit_id(target)
    assert ids(editor, fresh_target) == {annotation.annotation_id}
    assert ids(editor, untouched) == {other_annotation.annotation_id}
    assert reader.get_book("b") is other_book
    assert reader.list_chapters("b") is other_chapters
    assert next(item for item in reader.records if reader_edit_id(item) == reader_edit_id(untouched)) is untouched
    reader, editor = open_editor(tmp_path)
    fresh_target = next(item for item in reader.records if item.text == "beta text")
    assert ids(editor, fresh_target) == {annotation.annotation_id}


def test_invalid_later_mutation_does_not_change_text_annotations_or_cached_metadata(tmp_path):
    reader, editor = open_editor(tmp_path, make_records())
    target = reader.chapters[("a", 0)][0]
    annotation = add_annotation(editor, target)
    book = reader.get_book("a")
    chapters = reader.list_chapters("a")
    before = editor.state()
    with pytest.raises(KeyError):
        editor.apply_operation(ReaderOperationRequest(book_id="a", mutations=[
            ReaderMutation(edit_id=reader_edit_id(target), deleted=True),
            ReaderMutation(edit_id="missing", text="invalid"),
        ]))
    assert editor.state() == before
    assert reader.get_chapter("a", 0).paragraphs[0].text == target.text
    assert ids(editor, target) == {annotation.annotation_id}
    assert reader.get_book("a") is book
    assert reader.list_chapters("a") is chapters
