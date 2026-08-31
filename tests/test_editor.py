import json
from pathlib import Path

import pytest
from ebooklib import epub

from app.ingestion.reader_epub import load_epub_for_reader
from app.models.schemas import AnnotationCreateRequest, ReaderMutation, ReaderOperationRequest
from app.services.editor_session import EditorSessionService
from app.services.epub_editor import EpubEditorService
from app.services.reader import ReaderService, reader_edit_id


def make_epub(path: Path) -> None:
    book = epub.EpubBook()
    book.set_identifier("editor-test-book")
    book.set_title("编辑测试")
    book.set_language("zh")
    chapter = epub.EpubHtml(title="第一章", file_name="chapter.xhtml", lang="zh")
    chapter.content = "<html><body><h1>第一章</h1><p>保留正文</p><p>删除正文</p></body></html>"
    book.add_item(chapter)
    book.add_item(epub.EpubNcx())
    book.add_item(epub.EpubNav())
    book.toc = (epub.Link("chapter.xhtml", "第一章", "chapter"),)
    book.spine = ["nav", chapter]
    epub.write_epub(str(path), book)


def write_reader(path: Path, records) -> None:
    path.write_text(
        "".join(json.dumps(item.model_dump(mode="json"), ensure_ascii=False) + "\n" for item in records),
        encoding="utf-8",
    )


def test_epub_editor_updates_and_deletes_source_nodes(tmp_path):
    epub_path = tmp_path / "book.epub"
    make_epub(epub_path)
    records = load_epub_for_reader(epub_path)
    keep = next(item for item in records if item.text == "保留正文")
    remove = next(item for item in records if item.text == "删除正文")

    EpubEditorService(tmp_path).save(
        records,
        {reader_edit_id(keep): "修改后的正文"},
        {reader_edit_id(remove)},
    )

    updated = load_epub_for_reader(epub_path)
    texts = [item.text for item in updated]
    assert "修改后的正文" in texts
    assert "删除正文" not in texts


def test_editor_clears_pre_save_undo_stack_after_save(tmp_path):
    epub_path = tmp_path / "book.epub"
    reader_path = tmp_path / "reader.jsonl"
    annotation_path = tmp_path / "annotations.json"
    make_epub(epub_path)
    records = load_epub_for_reader(epub_path)
    write_reader(reader_path, records)
    reader = ReaderService(reader_path=reader_path, books_path=tmp_path / "missing.jsonl", chunks_path=tmp_path / "missing-chunks.jsonl")
    editor = EditorSessionService(reader)
    editor.annotation_path = annotation_path
    editor.epub_editor = EpubEditorService(tmp_path)
    editor.reset()
    targets = [item for item in records if item.text in {"保留正文", "删除正文"}]

    for index, target in enumerate(targets, start=1):
        editor.apply_operation(
            ReaderOperationRequest(
                book_id=target.book_id,
                label=f"操作 {index}",
                mutations=[ReaderMutation(edit_id=reader_edit_id(target), text=f"修改 {index}")],
            )
        )

    assert editor.state().undo_count == 2
    editor.save()
    assert editor.state().undo_count == 0
    assert not editor.state().dirty

    state = editor.undo()
    assert state.undo_count == 0
    assert not state.dirty
    effective = editor.apply_records(records)
    assert next(item.text for item in effective if reader_edit_id(item) == reader_edit_id(targets[1])) == "修改 2"


def test_batch_delete_and_annotation_are_undoable(tmp_path):
    epub_path = tmp_path / "book.epub"
    reader_path = tmp_path / "reader.jsonl"
    make_epub(epub_path)
    records = load_epub_for_reader(epub_path)
    write_reader(reader_path, records)
    reader = ReaderService(reader_path=reader_path, books_path=tmp_path / "missing.jsonl", chunks_path=tmp_path / "missing-chunks.jsonl")
    editor = EditorSessionService(reader)
    editor.annotation_path = tmp_path / "annotations.json"
    editor.epub_editor = EpubEditorService(tmp_path)
    editor.reset()
    targets = [item for item in records if item.text in {"保留正文", "删除正文"}]

    editor.apply_operation(
        ReaderOperationRequest(
            book_id=targets[0].book_id,
            label="删除 2 个段落",
            mutations=[ReaderMutation(edit_id=reader_edit_id(item), deleted=True) for item in targets],
        )
    )
    assert not {item.text for item in targets} & {item.text for item in editor.apply_records(records)}
    editor.undo()
    assert {item.text for item in targets} <= {item.text for item in editor.apply_records(records)}

    annotation, state = editor.add_annotation(
        AnnotationCreateRequest(
            book_id=targets[0].book_id,
            chapter_index=targets[0].chapter_index,
            start_edit_id=reader_edit_id(targets[0]),
            end_edit_id=reader_edit_id(targets[0]),
            start_offset=0,
            end_offset=2,
            selected_text=targets[0].text[:2],
            color="yellow",
            comment="测试批注",
        )
    )
    assert state.undo_count == 1
    assert editor.annotations_for_record(targets[0])[0].annotation_id == annotation.annotation_id
    editor.apply_operation(
        ReaderOperationRequest(
            book_id=targets[0].book_id,
            label="删除含批注段落",
            mutations=[ReaderMutation(edit_id=reader_edit_id(targets[0]), deleted=True)],
        )
    )
    assert editor.annotations_for_record(targets[0]) == []
    editor.undo()
    assert editor.annotations_for_record(targets[0])[0].annotation_id == annotation.annotation_id
    editor.undo()
    assert editor.annotations_for_record(targets[0]) == []


def test_chapter_delete_is_hidden_and_undoable(tmp_path):
    epub_path = tmp_path / "book.epub"
    reader_path = tmp_path / "reader.jsonl"
    make_epub(epub_path)
    records = load_epub_for_reader(epub_path)
    write_reader(reader_path, records)
    reader = ReaderService(reader_path=reader_path, books_path=tmp_path / "missing.jsonl", chunks_path=tmp_path / "missing-chunks.jsonl")
    editor = EditorSessionService(reader)
    editor.annotation_path = tmp_path / "annotations.json"
    editor.epub_editor = EpubEditorService(tmp_path)
    editor.reset()
    target = next(item for item in records if item.text == "保留正文")
    book_id = target.book_id
    chapter_index = target.chapter_index
    chapter_count = len(reader.list_chapters(book_id))

    state = editor.delete_chapter(book_id, chapter_index)

    assert state.dirty
    assert state.last_operation.startswith("删除章节：")
    assert chapter_index not in {item.chapter_index for item in reader.list_chapters(book_id)}
    assert reader.get_book(book_id).chapter_count == chapter_count - 1
    with pytest.raises(KeyError):
        reader.get_chapter(book_id, chapter_index)

    editor.undo()
    assert len(reader.list_chapters(book_id)) == chapter_count
    assert reader.get_chapter(book_id, chapter_index).paragraphs

    editor.delete_chapter(book_id, chapter_index)
    editor.save()
    assert editor.state().undo_count == 0
    saved_texts = {item.text for item in load_epub_for_reader(epub_path)}
    assert "保留正文" not in saved_texts
    assert "删除正文" not in saved_texts


def test_chapter_rename_updates_reader_epub_and_undo(tmp_path):
    epub_path = tmp_path / "book.epub"
    reader_path = tmp_path / "reader.jsonl"
    make_epub(epub_path)
    records = load_epub_for_reader(epub_path)
    write_reader(reader_path, records)
    reader = ReaderService(reader_path=reader_path, books_path=tmp_path / "missing.jsonl", chunks_path=tmp_path / "missing-chunks.jsonl")
    editor = EditorSessionService(reader)
    editor.annotation_path = tmp_path / "annotations.json"
    editor.epub_editor = EpubEditorService(tmp_path)
    editor.reset()
    target = next(item for item in records if item.text == "保留正文")
    original_title = target.chapter_title

    state = editor.rename_chapter(target.book_id, target.chapter_index, "修改后的章节名")
    assert state.dirty
    assert reader.get_chapter(target.book_id, target.chapter_index).chapter.chapter_title == "修改后的章节名"

    editor.undo()
    assert reader.get_chapter(target.book_id, target.chapter_index).chapter.chapter_title == original_title

    editor.rename_chapter(target.book_id, target.chapter_index, "修改后的章节名")
    saved_state = editor.save()
    assert saved_state.undo_count == 0
    updated = load_epub_for_reader(epub_path)
    updated_target = next(item for item in updated if item.text == "保留正文")
    assert updated_target.chapter_title == "修改后的章节名"
