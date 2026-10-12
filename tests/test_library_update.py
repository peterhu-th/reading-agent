import json
import os
from pathlib import Path

import pytest
from ebooklib import epub

from app.config import Settings
from app.ingestion import library_update
from app.models.schemas import ReaderMutation, ReaderOperationRequest
from app.services.editor_session import EditorSessionService
from app.services.epub_editor import EpubEditorService
from app.services.reader import ReaderService, reader_edit_id


def make_epub(path, title, extra=""):
    previous_mtime = path.stat().st_mtime_ns if path.exists() else None
    book = epub.EpubBook()
    book.set_identifier(title)
    book.set_title(title)
    book.set_language("zh")
    chapter = epub.EpubHtml(uid="chapter", title="第一章", file_name="chapter.xhtml", lang="zh")
    paragraphs = [f"第{index}段。" + (f"{title}的故事在山间继续，人物回忆此前经历，逐渐理解事情的来龙去脉。" * 12) + extra for index in range(3)]
    chapter.content = "<html><body><h1>第一章</h1>" + "".join(f"<p>{text}</p>" for text in paragraphs) + "</body></html>"
    book.add_item(chapter)
    book.add_item(epub.EpubNcx())
    book.add_item(epub.EpubNav())
    book.toc = (epub.Link("chapter.xhtml", "第一章", "chapter"),)
    book.spine = ["nav", chapter]
    epub.write_epub(str(path), book)
    if previous_mtime is not None:
        os.utime(path, ns=(previous_mtime + 1_000_000_000, previous_mtime + 1_000_000_000))


@pytest.fixture
def library(tmp_path):
    raw = tmp_path / "epub"
    raw.mkdir()
    make_epub(raw / "a.epub", "甲书")
    make_epub(raw / "b.epub", "乙书")
    return Settings(
        OPENAI_API_KEY="test", OPENAI_BASE_URL="http://127.0.0.1:1/v1",
        RAW_EPUB_DIR=str(raw),
        READER_JSONL_PATH=str(tmp_path / "reader" / "reader.jsonl"),
        BOOKS_JSONL_PATH=str(tmp_path / "paragraphs" / "books.jsonl"),
        CHUNKS_JSONL_PATH=str(tmp_path / "chunks" / "chunks.jsonl"),
        CHUNK_SIZE=200, CHUNK_OVERLAP=0,
    )


def output_paths(settings):
    return [Path(settings.READER_JSONL_PATH), Path(settings.BOOKS_JSONL_PATH), Path(settings.CHUNKS_JSONL_PATH)]


def state_path(settings):
    return Path(settings.READER_JSONL_PATH).with_name("library_update_state.json")


def rows(path):
    return [json.loads(line) for line in Path(path).read_text(encoding="utf-8").splitlines() if line.strip()]


def snapshot(paths):
    return {path: (path.stat().st_mtime_ns, path.read_bytes()) for path in paths}


def count_parses(monkeypatch):
    calls = []
    original = library_update.load_epub_for_reader

    def parse(path):
        calls.append(Path(path).name)
        return original(path)

    monkeypatch.setattr(library_update, "load_epub_for_reader", parse)
    return calls


def test_initial_import_parses_each_epub_once_and_unchanged_run_does_no_io_work(library, monkeypatch):
    calls = count_parses(monkeypatch)
    stats = library_update.update_library(library)
    assert calls == ["a.epub", "b.epub"]
    assert (stats.parsed_books, stats.projected_books, stats.rechunked_books) == (2, 2, 2)
    assert stats.reader_changed and stats.paragraphs_changed and stats.chunks_changed
    reader_locations = {(row["book_id"], row["chapter_index"], row["paragraph_index"]): row["text"] for row in rows(library.READER_JSONL_PATH)}
    for paragraph in rows(library.BOOKS_JSONL_PATH):
        assert reader_locations[(paragraph["book_id"], paragraph["chapter_index"], paragraph["paragraph_index"])] == paragraph["text"]
    before = snapshot(output_paths(library) + [state_path(library)])

    def unexpected(*_args, **_kwargs):
        raise AssertionError("unchanged library should skip parsing and JSONL reads/writes")

    monkeypatch.setattr(library_update, "load_epub_for_reader", unexpected)
    monkeypatch.setattr(library_update, "_read_jsonl", unexpected)
    monkeypatch.setattr(library_update, "_stage_jsonl", unexpected)
    stats = library_update.update_library(library)
    assert (stats.parsed_books, stats.reused_books, stats.rechunked_books) == (0, 2, 0)
    assert not stats.reader_changed and not stats.paragraphs_changed and not stats.chunks_changed
    assert stats.changed_book_ids == set()
    assert snapshot(output_paths(library) + [state_path(library)]) == before


def test_changing_one_book_reuses_the_other_book(library, monkeypatch):
    library_update.update_library(library)
    before_other = [row for row in rows(library.CHUNKS_JSONL_PATH) if row["title"] == "乙书"]
    make_epub(Path(library.RAW_EPUB_DIR) / "a.epub", "甲书", "新增的一段后续情节。")
    calls = count_parses(monkeypatch)
    stats = library_update.update_library(library)
    assert calls == ["a.epub"]
    assert (stats.parsed_books, stats.reused_books, stats.projected_books, stats.rechunked_books) == (1, 1, 1, 1)
    assert [row for row in rows(library.CHUNKS_JSONL_PATH) if row["title"] == "乙书"] == before_other
    assert {row["book_id"] for row in rows(library.READER_JSONL_PATH) if row["title"] == "甲书"} == stats.changed_book_ids


def test_deleted_books_are_removed_including_the_last_book(library, monkeypatch):
    library_update.update_library(library)
    deleted_id = next(row["book_id"] for row in rows(library.READER_JSONL_PATH) if row["title"] == "乙书")
    (Path(library.RAW_EPUB_DIR) / "b.epub").unlink()
    calls = count_parses(monkeypatch)
    stats = library_update.update_library(library)
    assert calls == []
    assert (stats.parsed_books, stats.reused_books, stats.deleted_books, stats.rechunked_books) == (0, 1, 1, 0)
    assert deleted_id in stats.changed_book_ids
    assert all({row["title"] for row in rows(path)} == {"甲书"} for path in output_paths(library))

    (Path(library.RAW_EPUB_DIR) / "a.epub").unlink()
    stats = library_update.update_library(library)
    assert stats.total_books == 0 and stats.deleted_books == 1
    assert stats.reader_changed and stats.paragraphs_changed and stats.chunks_changed
    assert all(path.read_bytes() == b"" for path in output_paths(library))
    assert json.loads(state_path(library).read_text(encoding="utf-8"))["sources"] == {}


def test_chunk_parameters_only_rechunk_and_leave_reader_and_paragraph_files_untouched(library, monkeypatch):
    library_update.update_library(library)
    before = snapshot(output_paths(library)[:2])
    calls = count_parses(monkeypatch)
    changed = library.model_copy(update={"CHUNK_SIZE": 300, "CHUNK_OVERLAP": 50})
    stats = library_update.update_library(changed)
    assert calls == []
    assert (stats.parsed_books, stats.projected_books, stats.rechunked_books) == (0, 0, 2)
    assert not stats.reader_changed and not stats.paragraphs_changed and stats.chunks_changed
    assert snapshot(output_paths(library)[:2]) == before


@pytest.mark.parametrize("missing", ["READER_JSONL_PATH", "BOOKS_JSONL_PATH", "CHUNKS_JSONL_PATH"])
def test_missing_output_is_recovered_without_skipping_sources(library, monkeypatch, missing):
    library_update.update_library(library)
    original = Path(getattr(library, missing)).read_bytes()
    Path(getattr(library, missing)).unlink()
    calls = count_parses(monkeypatch)
    stats = library_update.update_library(library)
    assert len(calls) == (2 if missing == "READER_JSONL_PATH" else 0)
    assert Path(getattr(library, missing)).read_bytes() == original
    assert stats.parsed_books + stats.reused_books == 2
    assert library_update.update_library(library).parsed_books == 0


def test_editor_save_reparses_only_the_changed_epub_despite_replacing_reader_jsonl(library, tmp_path, monkeypatch):
    library_update.update_library(library)
    reader = ReaderService(reader_path=library.READER_JSONL_PATH)
    editor = EditorSessionService(reader)
    editor.annotation_path = tmp_path / "annotations.json"
    editor.bookmark_path = tmp_path / "bookmarks.json"
    editor.epub_editor = EpubEditorService(library.RAW_EPUB_DIR)
    editor.reset()
    target = next(item for item in reader.records if item.title == "甲书" and len(item.text) > 100)
    editor.apply_operation(ReaderOperationRequest(book_id=target.book_id, mutations=[
        ReaderMutation(edit_id=reader_edit_id(target), text=target.text + "编辑器保存的新增情节。"),
    ]))
    editor.save()
    calls = count_parses(monkeypatch)
    stats = library_update.update_library(library)
    assert calls == ["a.epub"]
    assert (stats.parsed_books, stats.reused_books, stats.projected_books, stats.rechunked_books) == (1, 1, 1, 1)
    assert not stats.reader_changed and stats.paragraphs_changed and stats.chunks_changed
    assert any("编辑器保存的新增情节" in row["text"] for row in rows(library.CHUNKS_JSONL_PATH))


def test_reader_modified_without_epub_change_is_restored_from_epub(library, monkeypatch):
    library_update.update_library(library)
    path = Path(library.READER_JSONL_PATH)
    original = rows(path)
    changed = rows(path)
    changed[-1]["text"] = "外部改写了阅读文件而未更新原始 EPUB。"
    path.write_text("".join(json.dumps(row) + "\n" for row in changed), encoding="utf-8")
    calls = count_parses(monkeypatch)
    stats = library_update.update_library(library)
    assert calls == ["a.epub", "b.epub"]
    assert stats.reader_changed
    assert rows(path) == original


def test_force_reparses_but_does_not_rewrite_identical_jsonl(library, monkeypatch):
    library_update.update_library(library)
    before = snapshot(output_paths(library))
    calls = count_parses(monkeypatch)
    stats = library_update.update_library(library, force=True)
    assert calls == ["a.epub", "b.epub"]
    assert stats.projected_books == stats.rechunked_books == 2
    assert not stats.reader_changed and not stats.paragraphs_changed and not stats.chunks_changed
    assert snapshot(output_paths(library)) == before


def test_failed_state_replacement_restores_outputs_and_does_not_mark_source_current(library, monkeypatch):
    library_update.update_library(library)
    make_epub(Path(library.RAW_EPUB_DIR) / "a.epub", "甲书", "此次变更必须在重试后处理。")
    before = snapshot(output_paths(library) + [state_path(library)])
    original_replace = Path.replace

    def fail_state(path, destination):
        if Path(destination) == state_path(library):
            raise PermissionError("state file unavailable")
        return original_replace(path, destination)

    with monkeypatch.context() as patch:
        patch.setattr(Path, "replace", fail_state)
        with pytest.raises(PermissionError):
            library_update.update_library(library)
    assert {path: path.read_bytes() for path in before} == {path: content for path, (_mtime, content) in before.items()}
    calls = count_parses(monkeypatch)
    stats = library_update.update_library(library)
    assert calls == ["a.epub"]
    assert stats.chunks_changed
    assert any("此次变更必须在重试后处理" in row["text"] for row in rows(library.BOOKS_JSONL_PATH))


def test_epub_changed_during_import_leaves_previous_outputs_and_state_intact(library, monkeypatch):
    library_update.update_library(library)
    path = Path(library.RAW_EPUB_DIR) / "a.epub"
    make_epub(path, "甲书", "解析时发生新的保存。")
    before = snapshot(output_paths(library) + [state_path(library)])
    original_parse = library_update.load_epub_for_reader

    def parse_and_change(source):
        records = original_parse(source)
        stamp = source.stat().st_mtime_ns + 1_000_000_000
        os.utime(source, ns=(stamp, stamp))
        return records

    monkeypatch.setattr(library_update, "load_epub_for_reader", parse_and_change)
    with pytest.raises(RuntimeError, match="EPUB"):
        library_update.update_library(library)
    assert snapshot(output_paths(library) + [state_path(library)]) == before


def test_switching_to_empty_source_directory_removes_previous_library(library, tmp_path):
    library_update.update_library(library)
    empty = tmp_path / "empty-epubs"
    empty.mkdir()
    stats = library_update.update_library(library.model_copy(update={"RAW_EPUB_DIR": str(empty)}))
    assert stats.total_books == 0 and stats.deleted_books == 2
    assert all(path.read_bytes() == b"" for path in output_paths(library))


def test_missing_source_directory_does_not_clear_existing_outputs(library, tmp_path):
    library_update.update_library(library)
    before = snapshot(output_paths(library) + [state_path(library)])
    with pytest.raises(FileNotFoundError, match="EPUB"):
        library_update.update_library(library.model_copy(update={"RAW_EPUB_DIR": str(tmp_path / "missing")}))
    assert snapshot(output_paths(library) + [state_path(library)]) == before
