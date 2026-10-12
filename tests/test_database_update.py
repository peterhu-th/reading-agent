import json
import os
from pathlib import Path
from types import SimpleNamespace

import pytest

from app.config import Settings
from app.ingestion import library_update
from app.models.schemas import ReaderContentRecord
from app.services import database_update as update_service
from scripts import update_database


@pytest.fixture
def pipeline(tmp_path, monkeypatch):
    settings = Settings(
        OPENAI_API_KEY="offline-test", OPENAI_BASE_URL="http://127.0.0.1:1/v1",
        RAW_EPUB_DIR=str(tmp_path / "epub"),
        READER_JSONL_PATH=str(tmp_path / "reader.jsonl"),
        BOOKS_JSONL_PATH=str(tmp_path / "books.jsonl"),
        CHUNKS_JSONL_PATH=str(tmp_path / "chunks.jsonl"),
        SUMMARIES_JSONL_PATH=str(tmp_path / "summaries.jsonl"),
        VECTOR_DB_PATH=str(tmp_path / "chroma"),
        EMBEDDING_BACKEND="sentence-transformers", EMBEDDING_MODEL="model-a",
    )
    raw = Path(settings.RAW_EPUB_DIR)
    raw.mkdir()
    for name in ("a", "b"):
        (raw / f"{name}.epub").write_text(name, encoding="utf-8")
    parsed = []

    def parse(path):
        parsed.append(path.name)
        return [ReaderContentRecord(
            book_id=path.stem, title=path.stem, source_epub=path.name,
            source_href="chapter.xhtml", source_node_path="0/1/0",
            chapter_index=0, chapter_title="第一章", paragraph_index=0,
            text=path.read_text(encoding="utf-8") + "人在旅途中经历了许多事情，逐渐理解了生活与故乡的意义。" * 30,
        )]

    monkeypatch.setattr(library_update, "load_epub_for_reader", parse)
    monkeypatch.setattr(update_database, "get_settings", lambda: settings)

    class RecordedUpdater(update_database.DatabaseUpdater):
        def __init__(self):
            self.messages = []
            super().__init__(output=self.messages.append)
            self.calls = []
            self.summary_scopes = []
            self.resets = []
            self.fail_stage = None

        def _summary_update(self, *, changed_book_ids, force):
            self.calls.append("summaries")
            self.summary_scopes.append((changed_book_ids, force))
            if self.fail_stage == "summaries":
                raise RuntimeError("summary failed")
            target = Path(settings.SUMMARIES_JSONL_PATH)
            contents = Path(settings.CHUNKS_JSONL_PATH).read_text(encoding="utf-8")
            contents += json.dumps(update_database.summary_config(settings))
            if not target.exists() or target.read_text(encoding="utf-8") != contents:
                target.write_text(contents, encoding="utf-8")
            return SimpleNamespace(generated_records=1, deleted_records=0)

        def _index(self, stage):
            self.calls.append(stage)
            if self.fail_stage == stage:
                raise RuntimeError(f"{stage} failed")
            database = Path(settings.VECTOR_DB_PATH) / "chroma.sqlite3"
            database.parent.mkdir(exist_ok=True)
            with database.open("ab") as stream:
                stream.write(stage.encode())
            return SimpleNamespace(indexed_chunks=1, skipped_chunks=0, deleted_chunks=0)

        def _index_chunks(self, *, force_reset=False):
            if force_reset:
                self.resets.append("chunk_index")
            return self._index("chunk_index")

        def _index_summaries(self, *, force_reset=False):
            if force_reset:
                self.resets.append("summary_index")
            return self._index("summary_index")

    updater = RecordedUpdater()
    return updater, settings, parsed


def test_unchanged_update_skips_parsing_and_all_downstream_work_without_writes(pipeline):
    updater, settings, parsed = pipeline
    assert updater.update_all() == {"library", "summaries", "chunk_index", "summary_index"}
    assert parsed == ["a.epub", "b.epub"]
    before = {path: update_database.file_state(path) for path in Path(settings.READER_JSONL_PATH).parent.glob("*.json*")}
    parsed.clear()
    updater.calls.clear()
    updater.messages.clear()

    assert updater.update_all() == set()

    assert parsed == updater.calls == []
    assert len(updater.messages) == 2
    assert "跳过全部处理" in updater.messages[-1]
    assert {path: update_database.file_state(path) for path in before} == before


def test_one_changed_book_only_reparses_that_book_and_scopes_summary_work(pipeline):
    updater, settings, parsed = pipeline
    updater.update_all()
    parsed.clear()
    updater.calls.clear()
    (Path(settings.RAW_EPUB_DIR) / "a.epub").write_text("updated a", encoding="utf-8")

    updater.update_all()

    assert parsed == ["a.epub"]
    assert updater.summary_scopes[-1] == ({"a"}, False)
    assert updater.calls == ["summaries", "chunk_index", "summary_index"]


def test_touched_epub_with_identical_contents_reports_check_without_downstream_work(pipeline):
    updater, settings, parsed = pipeline
    updater.update_all()
    source = Path(settings.RAW_EPUB_DIR) / "a.epub"
    stamp = source.stat()
    os.utime(source, ns=(stamp.st_atime_ns, stamp.st_mtime_ns + 1_000_000_000))
    parsed.clear()
    updater.calls.clear()
    updater.messages.clear()

    assert updater.update_all() == {"library"}
    assert parsed == ["a.epub"]
    assert updater.calls == []
    assert any("解析 1 本" in message for message in updater.messages)
    assert all("跳过全部处理" not in message for message in updater.messages)


def test_configuration_changes_run_only_dependent_stages(pipeline):
    updater, settings, parsed = pipeline
    updater.update_all()
    parsed.clear()
    updater.calls.clear()
    settings.SUMMARY_CHAPTER_TARGET_CHARS += 50
    updater.update_all()
    assert updater.calls == ["summaries", "summary_index"]
    assert updater.summary_scopes[-1] == (None, True)
    assert parsed == []

    updater.calls.clear()
    settings.EMBEDDING_MODEL = "model-b"
    updater.update_all()
    assert updater.calls == ["chunk_index", "summary_index"]

    updater.calls.clear()
    settings.EMBEDDING_BATCH_SIZE += 1
    assert updater.update_all() == set()
    assert updater.calls == []


def test_failed_summaries_are_retried_after_ingestion_is_already_cached(pipeline):
    updater, settings, _ = pipeline
    updater.update_all()
    (Path(settings.RAW_EPUB_DIR) / "a.epub").write_text("updated", encoding="utf-8")
    updater.fail_stage = "summaries"
    with pytest.raises(RuntimeError, match="summary failed"):
        updater.update_all()
    updater.fail_stage = None
    updater.calls.clear()

    updater.update_all()

    assert updater.summary_scopes[-1] == (None, False)
    assert updater.calls == ["summaries", "chunk_index", "summary_index"]


def test_external_index_change_then_partial_failure_does_not_mark_other_index_current(pipeline):
    updater, settings, _ = pipeline
    updater.update_all()
    database = Path(settings.VECTOR_DB_PATH) / "chroma.sqlite3"
    database.write_bytes(b"externally reset")
    updater.fail_stage = "summary_index"
    with pytest.raises(RuntimeError, match="summary_index failed"):
        updater.update_all()
    updater.fail_stage = None
    updater.calls.clear()

    updater.update_all()

    assert updater.calls == ["summary_index"]


def test_missing_vector_segment_invalidates_index_skip_state(pipeline):
    updater, settings, _ = pipeline
    directory = Path(settings.VECTOR_DB_PATH)
    directory.mkdir()
    segment = directory / "segment.bin"
    segment.write_bytes(b"persisted vectors")
    updater.update_all()
    updater.calls.clear()
    segment.unlink()

    updater.update_all()

    assert updater.calls == ["chunk_index", "summary_index"]
    assert updater.resets == ["chunk_index", "summary_index"]


def test_wal_change_triggers_index_checks_without_touching_main_database(pipeline):
    updater, settings, _ = pipeline
    updater.update_all()
    database = Path(settings.VECTOR_DB_PATH) / "chroma.sqlite3"
    before = update_database.file_state(database)
    database.with_name("chroma.sqlite3-wal").write_bytes(b"committed transaction")
    assert update_database.file_state(database) == before
    updater.calls.clear()
    updater.update_all()
    assert updater.calls == ["chunk_index", "summary_index"]


def test_external_index_change_during_summary_generation_is_rechecked(pipeline, monkeypatch):
    updater, settings, _ = pipeline
    updater.update_all()
    settings.SUMMARY_CHAPTER_TARGET_CHARS += 20
    original = updater._summary_update

    def update_and_replace_database(**kwargs):
        result = original(**kwargs)
        (Path(settings.VECTOR_DB_PATH) / "chroma.sqlite3").write_bytes(b"externally reset")
        return result

    monkeypatch.setattr(updater, "_summary_update", update_and_replace_database)
    updater.calls.clear()
    updater.update_all()
    assert updater.calls == ["summaries", "chunk_index", "summary_index"]


def test_failed_segment_repair_keeps_remaining_reset_pending(pipeline):
    updater, settings, _ = pipeline
    directory = Path(settings.VECTOR_DB_PATH)
    directory.mkdir()
    segment = directory / "segment.bin"
    segment.write_bytes(b"persisted vectors")
    updater.update_all()
    segment.unlink()
    updater.fail_stage = "summary_index"
    with pytest.raises(RuntimeError, match="summary_index failed"):
        updater.update_all()
    updater.calls.clear()
    updater.resets.clear()
    updater.fail_stage = None
    updater.update_all()
    assert updater.calls == ["summary_index"]
    assert updater.resets == ["summary_index"]


def test_missing_summary_file_recovers_without_reparsing_books(pipeline):
    updater, settings, parsed = pipeline
    updater.update_all()
    parsed.clear()
    updater.calls.clear()
    Path(settings.SUMMARIES_JSONL_PATH).unlink()
    updater.update_all()
    assert parsed == []
    assert updater.calls == ["summaries", "summary_index"]


def test_force_checks_every_stage_and_corrupt_state_can_be_rebuilt(pipeline):
    updater, _, parsed = pipeline
    updater.update_all()
    parsed.clear()
    updater.calls.clear()
    updater.force = True
    updater.update_all()
    assert parsed == ["a.epub", "b.epub"]
    assert updater.calls == ["summaries", "chunk_index", "summary_index"]
    updater.force = False
    updater.state_path.write_text("incomplete json", encoding="utf-8")
    updater.calls.clear()
    updater.update_all()
    assert updater.calls == ["summaries", "chunk_index", "summary_index"]
    assert any("更新记录无法读取" in message for message in updater.messages)


def test_web_update_status_preserves_no_change_message(tmp_path, monkeypatch):
    final_message = "书库和配置均未变化，已跳过全部处理，耗时 0.1 秒。"
    process = SimpleNamespace(stdout=iter(["检查书籍和更新配置…\n", final_message + "\n"]), wait=lambda: 0)
    monkeypatch.setattr(update_service.subprocess, "Popen", lambda *args, **kwargs: process)
    service = update_service.DatabaseUpdateService(tmp_path)
    service._run()
    assert service.state()["status"] == "complete"
    assert service.state()["message"] == final_message
    assert not service.state()["running"]
