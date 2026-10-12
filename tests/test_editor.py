import json
from pathlib import Path
from zipfile import ZipFile

import pytest
from ebooklib import epub

from app.ingestion.reader_epub import load_epub_for_reader
from app.models.schemas import AnnotationCreateRequest, ReaderMutation, ReaderOperationRequest
from app.services.editor_session import EditorSessionService
from app.services.epub_editor import EpubEditorService
from app.services.reader import ReaderService, reader_edit_id


def make_epub(path: Path, content: str = "<html><body><h1>第一章</h1><p>保留正文</p><p>删除正文</p></body></html>") -> None:
    book = epub.EpubBook()
    book.set_identifier("editor-test-book")
    book.set_title("编辑测试")
    book.set_language("zh")
    chapter = epub.EpubHtml(title="第一章", file_name="chapter.xhtml", lang="zh")
    chapter.content = content
    book.add_item(chapter)
    book.add_item(epub.EpubNcx())
    book.add_item(epub.EpubNav())
    book.toc = (epub.Link("chapter.xhtml", "第一章", "chapter"),)
    book.spine = ["nav", chapter]
    epub.write_epub(str(path), book)


def make_nonstandard_epub(path: Path) -> None:
    book = epub.EpubBook()
    book.set_identifier("fallback-editor-test-book")
    book.set_title("非标准标签编辑测试")
    book.set_language("zh")
    chapter = epub.EpubHtml(title="目录页", file_name="contents.xhtml", lang="zh")
    chapter.content = "<html><body><div>目录</div><div><a href='chapter.xhtml'>第一章</a></div></body></html>"
    book.add_item(chapter)
    book.add_item(epub.EpubNcx())
    book.add_item(epub.EpubNav())
    book.toc = (epub.Link("contents.xhtml", "目录页", "contents"),)
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


def test_nonstandard_reader_blocks_receive_editable_node_paths(tmp_path):
    epub_path = tmp_path / "fallback.epub"
    make_nonstandard_epub(epub_path)

    records = load_epub_for_reader(epub_path)

    assert records
    assert all(record.source_node_path for record in records)


def test_chapter_delete_saves_legacy_records_without_node_paths(tmp_path):
    epub_path = tmp_path / "fallback.epub"
    make_nonstandard_epub(epub_path)
    records = load_epub_for_reader(epub_path)
    legacy_records = [record.model_copy(update={"source_node_path": ""}) for record in records]
    target_chapter = next(record.chapter_index for record in legacy_records if record.source_href == "contents.xhtml")
    target_records = [record for record in legacy_records if record.chapter_index == target_chapter]

    EpubEditorService(tmp_path).save(
        legacy_records,
        {},
        {reader_edit_id(record) for record in target_records},
    )

    assert all(record.source_href != "contents.xhtml" for record in load_epub_for_reader(epub_path))


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
    editor.bookmark_path = tmp_path / "bookmarks.json"
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
    effective = editor.apply_records(reader.records)
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
    editor.bookmark_path = tmp_path / "bookmarks.json"
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
    editor.bookmark_path = tmp_path / "bookmarks.json"
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
    editor.bookmark_path = tmp_path / "bookmarks.json"
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


def open_editor(tmp_path):
    reader = ReaderService(reader_path=tmp_path / "reader.jsonl", books_path=tmp_path / "missing.jsonl", chunks_path=tmp_path / "missing-chunks.jsonl")
    editor = EditorSessionService(reader)
    editor.annotation_path = tmp_path / "annotations.json"
    editor.bookmark_path = tmp_path / "bookmarks.json"
    editor.epub_editor = EpubEditorService(tmp_path)
    editor.reset()
    return reader, editor


def test_delete_save_restart_keeps_reader_locations_and_annotations(tmp_path):
    epub_path = tmp_path / "book.epub"
    make_epub(epub_path)
    write_reader(tmp_path / "reader.jsonl", load_epub_for_reader(epub_path))
    reader, editor = open_editor(tmp_path)
    heading = next(item for item in reader.records if item.source_href == "chapter.xhtml" and item.kind == "heading")
    target = next(item for item in reader.records if item.text == "保留正文")
    old_id = reader_edit_id(target)
    annotation, _ = editor.add_annotation(AnnotationCreateRequest(
        book_id=target.book_id, chapter_index=target.chapter_index,
        start_edit_id=old_id, end_edit_id=old_id, start_offset=0, end_offset=2,
        selected_text=target.text[:2], comment="保留此批注",
    ))
    editor.apply_operation(ReaderOperationRequest(book_id=heading.book_id, mutations=[ReaderMutation(edit_id=reader_edit_id(heading), deleted=True)]))

    state = editor.save()

    assert not state.dirty and state.undo_count == 0
    assert reader.records == load_epub_for_reader(epub_path)
    assert ReaderService._load_reader_records(tmp_path / "reader.jsonl") == reader.records
    target = next(item for item in reader.records if item.text == "保留正文")
    assert reader_edit_id(target) != old_id
    migrated = editor.annotations_for_record(target)[0]
    assert migrated.annotation_id == annotation.annotation_id
    assert migrated.start_edit_id == migrated.end_edit_id == reader_edit_id(target)
    assert (migrated.start_offset, migrated.end_offset) == (0, 2)

    epub_before_noop = epub_path.read_bytes()
    editor.save()
    assert epub_path.read_bytes() == epub_before_noop
    for text in ("第一次修改", "再次修改"):
        reader, editor = open_editor(tmp_path)
        target = next(item for item in reader.records if item.source_href == "chapter.xhtml" and item.paragraph_index == 0)
        assert editor.annotations_for_record(target)[0].annotation_id == annotation.annotation_id
        editor.apply_operation(ReaderOperationRequest(book_id=target.book_id, mutations=[ReaderMutation(edit_id=reader_edit_id(target), text=text)]))
        editor.save()
        assert [item.text for item in load_epub_for_reader(epub_path) if item.source_href == "chapter.xhtml"] == [text, "删除正文"]
        assert reader.records == load_epub_for_reader(epub_path)


def test_delete_line_reindexes_surviving_lines_before_next_edit(tmp_path):
    epub_path = tmp_path / "book.epub"
    make_epub(epub_path, "<html><body><h1>第一章</h1><p>第一行<br/>第二行<br/>第三行</p></body></html>")
    write_reader(tmp_path / "reader.jsonl", load_epub_for_reader(epub_path))
    reader, editor = open_editor(tmp_path)
    removed = next(item for item in reader.records if item.text == "第一行")
    last = next(item for item in reader.records if item.text == "第三行")
    editor.add_annotation(AnnotationCreateRequest(
        book_id=last.book_id, chapter_index=last.chapter_index,
        start_edit_id=reader_edit_id(last), end_edit_id=reader_edit_id(last), end_offset=3,
    ))
    editor.apply_operation(ReaderOperationRequest(book_id=removed.book_id, mutations=[ReaderMutation(edit_id=reader_edit_id(removed), deleted=True)]))
    editor.save()

    reader, editor = open_editor(tmp_path)
    target = next(item for item in reader.records if item.text == "第二行")
    last = next(item for item in reader.records if item.text == "第三行")
    assert (target.source_line_index, last.source_line_index) == (0, 1)
    assert editor.annotations_for_record(last)[0].start_edit_id == reader_edit_id(last)
    editor.apply_operation(ReaderOperationRequest(book_id=target.book_id, mutations=[ReaderMutation(edit_id=reader_edit_id(target), text="修改第二行")]))
    editor.save()
    assert [item.text for item in load_epub_for_reader(epub_path) if item.source_href == "chapter.xhtml"] == ["第一章", "修改第二行", "第三行"]


def test_save_keeps_ignored_nodes_and_untouched_inline_markup(tmp_path):
    epub_path = tmp_path / "book.epub"
    make_epub(epub_path, "<html><body><script>var untouched = 1;</script><h1>第一章</h1><p>保留正文</p><p><em>强调</em>正文</p></body></html>")
    write_reader(tmp_path / "reader.jsonl", load_epub_for_reader(epub_path))
    reader, editor = open_editor(tmp_path)
    heading = next(item for item in reader.records if item.kind == "heading" and item.source_href == "chapter.xhtml")
    editor.apply_operation(ReaderOperationRequest(book_id=heading.book_id, mutations=[ReaderMutation(edit_id=reader_edit_id(heading), deleted=True)]))
    editor.save()

    reader, editor = open_editor(tmp_path)
    target = next(item for item in reader.records if item.text == "保留正文")
    editor.apply_operation(ReaderOperationRequest(book_id=target.book_id, mutations=[ReaderMutation(edit_id=reader_edit_id(target), text="正确修改")]))
    editor.save()
    assert [item.text for item in load_epub_for_reader(epub_path) if item.source_href == "chapter.xhtml"] == ["正确修改", "强调正文"]
    with ZipFile(epub_path) as archive:
        content = archive.read("EPUB/chapter.xhtml").decode("utf-8")
    assert "<em>强调</em>" in content
    assert "var untouched = 1;" in content


def test_delete_chapter_migrates_annotation_to_new_chapter_index(tmp_path):
    epub_path = tmp_path / "book.epub"
    make_epub(epub_path)
    book = epub.read_epub(str(epub_path))
    second = epub.EpubHtml(uid="second", title="第二章", file_name="second.xhtml", lang="zh")
    second.content = "<html><body><h1>第二章</h1><p>第二章正文</p></body></html>"
    book.add_item(second)
    book.spine.append(second)
    book.toc = (epub.Link("chapter.xhtml", "第一章", "chapter"), epub.Link("second.xhtml", "第二章", "second"))
    epub.write_epub(str(epub_path), book)
    write_reader(tmp_path / "reader.jsonl", load_epub_for_reader(epub_path))
    reader, editor = open_editor(tmp_path)
    removed = next(item for item in reader.records if item.text == "保留正文")
    target = next(item for item in reader.records if item.text == "第二章正文")
    previous_chapter = target.chapter_index
    editor.add_annotation(AnnotationCreateRequest(
        book_id=target.book_id, chapter_index=target.chapter_index,
        start_edit_id=reader_edit_id(target), end_edit_id=reader_edit_id(target), end_offset=3,
    ))
    editor.delete_chapter(removed.book_id, removed.chapter_index)
    editor.save()

    reader, editor = open_editor(tmp_path)
    target = next(item for item in reader.records if item.text == "第二章正文")
    assert target.chapter_index == previous_chapter - 1
    assert editor.annotations_for_record(target)[0].chapter_index == target.chapter_index
    assert reader.records == load_epub_for_reader(epub_path)


@pytest.mark.parametrize("failure_phase", ["staging", "replacement"])
def test_failed_save_keeps_files_working_edits_and_undo(tmp_path, monkeypatch, failure_phase):
    epub_path = tmp_path / "book.epub"
    reader_path = tmp_path / "reader.jsonl"
    make_epub(epub_path)
    write_reader(reader_path, load_epub_for_reader(epub_path))
    reader, editor = open_editor(tmp_path)
    editor.save()
    heading = next(item for item in reader.records if item.source_href == "chapter.xhtml" and item.kind == "heading")
    target = next(item for item in reader.records if item.text == "保留正文")
    editor.add_annotation(AnnotationCreateRequest(
        book_id=target.book_id, chapter_index=target.chapter_index,
        start_edit_id=reader_edit_id(target), end_edit_id=reader_edit_id(target), end_offset=2,
    ))
    editor.apply_operation(ReaderOperationRequest(book_id=heading.book_id, mutations=[ReaderMutation(edit_id=reader_edit_id(heading), deleted=True)]))
    originals = {path: path.read_bytes() for path in (epub_path, reader_path, editor.annotation_path)}
    state_before = editor.state()
    with monkeypatch.context() as patch:
        if failure_phase == "staging":
            def fail_write(_annotations):
                raise OSError("annotation storage unavailable")
            patch.setattr(editor, "_write_annotations", fail_write)
        else:
            original_replace = Path.replace
            def fail_replace(path, destination):
                if Path(destination) == editor.annotation_path:
                    raise PermissionError("annotation replacement unavailable")
                return original_replace(path, destination)
            patch.setattr(Path, "replace", fail_replace)
        with pytest.raises(OSError):
            editor.save()

    assert editor.state() == state_before
    assert editor.has_unsaved_changes
    assert all(path.read_bytes() == contents for path, contents in originals.items())
    assert heading not in editor.apply_records(reader.records)
    assert editor.annotations_for_record(target)[0].start_edit_id == reader_edit_id(target)
    editor.undo()
    assert heading in editor.apply_records(reader.records)
    editor.save()
    assert not editor.has_unsaved_changes


def test_middle_paragraph_edit_keeps_cross_paragraph_annotation_endpoints(tmp_path):
    epub_path = tmp_path / "book.epub"
    make_epub(epub_path, "<html><body><p>abcdefghij</p><p>x</p><p>klmnopqrst</p></body></html>")
    write_reader(tmp_path / "reader.jsonl", load_epub_for_reader(epub_path))
    reader, editor = open_editor(tmp_path)
    start, middle, end = [item for item in reader.records if item.source_href == "chapter.xhtml"]
    editor.add_annotation(AnnotationCreateRequest(
        book_id=start.book_id, chapter_index=start.chapter_index,
        start_edit_id=reader_edit_id(start), end_edit_id=reader_edit_id(end), start_offset=5, end_offset=8,
    ))
    editor.apply_operation(ReaderOperationRequest(book_id=middle.book_id, mutations=[ReaderMutation(edit_id=reader_edit_id(middle), text="xy")]))
    annotation = editor.annotations_for_record(start)[0]
    assert (annotation.start_offset, annotation.end_offset) == (5, 8)


def test_edit_second_identical_paragraph_survives_save_and_restart(tmp_path):
    epub_path = tmp_path / "book.epub"
    make_epub(epub_path, "<html><body><p>重复正文</p><p>重复正文</p></body></html>")
    write_reader(tmp_path / "reader.jsonl", load_epub_for_reader(epub_path))
    reader, editor = open_editor(tmp_path)
    first, second = [item for item in reader.records if item.source_href == "chapter.xhtml"]
    assert reader_edit_id(first) != reader_edit_id(second)
    editor.apply_operation(ReaderOperationRequest(
        book_id=second.book_id,
        mutations=[ReaderMutation(edit_id=reader_edit_id(second), text="仅修改第二段")],
    ))
    editor.save()

    reader, _editor = open_editor(tmp_path)
    expected = ["重复正文", "仅修改第二段"]
    assert [item.text for item in reader.records if item.source_href == "chapter.xhtml"] == expected
    assert [item.text for item in load_epub_for_reader(epub_path) if item.source_href == "chapter.xhtml"] == expected
