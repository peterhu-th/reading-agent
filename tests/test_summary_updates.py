import json
from pathlib import Path
from types import SimpleNamespace

import pytest

from app.config import Settings
from app.models.schemas import TextChunk
from scripts import build_summaries


@pytest.fixture
def settings(tmp_path, monkeypatch):
    value = Settings(
        OPENAI_API_KEY="test",
        OPENAI_BASE_URL="http://127.0.0.1:1/v1",
        CHUNKS_JSONL_PATH=str(tmp_path / "chunks.jsonl"),
        SUMMARIES_JSONL_PATH=str(tmp_path / "summaries.jsonl"),
        SUMMARY_BACKEND="extractive",
        SUMMARY_MODEL="summary-a",
    )
    monkeypatch.setattr(build_summaries, "get_settings", lambda: value)
    return value


def chunk(book_id="book-a", text_suffix=""):
    return TextChunk(
        chunk_id=f"{book_id}:0:0",
        book_id=book_id,
        title=book_id,
        author="作者",
        chapter_index=0,
        chapter_title="第一章",
        chunk_index=0,
        start_paragraph_index=0,
        end_paragraph_index=0,
        text="主人公沿着河流走过村庄，在漫长的旅程中寻找往日失散的伙伴，终于在远方的小城重新见到了熟悉的面孔。" + text_suffix,
    )


def fake_llm(monkeypatch):
    calls = []

    def invoke(prompt):
        calls.append(prompt)
        return SimpleNamespace(content="这段摘要概括主人公的经历与内心变化，同时介绍他的生活处境、人物关系和情节发展的主要线索。" * 3)

    monkeypatch.setattr(build_summaries, "make_llm", lambda: SimpleNamespace(invoke=invoke))
    return calls


def reject_llm(monkeypatch):
    def unexpected():
        raise AssertionError("An unchanged or empty summary update must not construct an LLM")

    monkeypatch.setattr(build_summaries, "make_llm", unexpected)


def test_unchanged_llm_summaries_do_not_construct_client_or_rewrite(settings, monkeypatch):
    chunks = [chunk()]
    fake_llm(monkeypatch)
    build_summaries.update_summaries(chunks=chunks, backend="llm")
    path = Path(settings.SUMMARIES_JSONL_PATH)
    before = path.stat().st_mtime_ns, path.read_bytes()
    reject_llm(monkeypatch)

    stats = build_summaries.update_summaries(chunks=chunks, backend="llm")

    assert stats.generated_records == stats.deleted_records == stats.pending_records == 0
    assert not stats.written
    assert (path.stat().st_mtime_ns, path.read_bytes()) == before


def test_changed_book_only_is_analyzed_and_deleted_book_summaries_are_removed(settings, monkeypatch):
    original_chunks = [chunk("changed"), chunk("deleted"), chunk("untouched")]
    build_summaries.update_summaries(chunks=original_chunks)
    before = build_summaries.load_existing(settings.SUMMARIES_JSONL_PATH)
    analyzed = []
    collect = build_summaries.collect_sentences

    def track(chunks):
        analyzed.extend(item.book_id for item in chunks)
        return collect(chunks)

    monkeypatch.setattr(build_summaries, "collect_sentences", track)
    current = [chunk("changed", "他随后重新回到故乡。"), chunk("untouched")]

    stats = build_summaries.update_summaries(chunks=current, changed_book_ids={"changed", "deleted"})

    after = build_summaries.load_existing(settings.SUMMARIES_JSONL_PATH)
    assert set(analyzed) == {"changed"}
    assert stats.generated_records == 2
    assert stats.deleted_records == 2
    assert stats.total_records == 4
    assert after["untouched:book"] == before["untouched:book"]
    assert after["untouched:chapter:0"] == before["untouched:chapter:0"]
    assert all(record.book_id != "deleted" for record in after.values())


def test_llm_model_change_rechecks_all_books_despite_limited_changed_ids(settings, monkeypatch):
    chunks = [chunk("a"), chunk("b")]
    fake_llm(monkeypatch)
    build_summaries.update_summaries(chunks=chunks, backend="llm")
    settings.SUMMARY_MODEL = "summary-b"
    calls = fake_llm(monkeypatch)

    stats = build_summaries.update_summaries(chunks=chunks, backend="llm", changed_book_ids={"a"})

    assert stats.generated_records == len(calls) == 4
    assert {record.summary_model for record in build_summaries.load_existing(settings.SUMMARIES_JSONL_PATH).values()} == {"summary-b"}


def test_deleting_book_only_does_not_construct_llm(settings, monkeypatch):
    fake_llm(monkeypatch)
    build_summaries.update_summaries(chunks=[chunk("keep"), chunk("deleted")], backend="llm")
    reject_llm(monkeypatch)

    stats = build_summaries.update_summaries(
        chunks=[chunk("keep")], backend="llm", changed_book_ids={"deleted"},
    )

    assert stats.generated_records == 0
    assert stats.deleted_records == 2
    assert stats.total_records == 2
    assert {record.book_id for record in build_summaries.load_existing(settings.SUMMARIES_JSONL_PATH).values()} == {"keep"}


def test_extractive_summaries_ignore_answer_model_changes(settings, monkeypatch):
    chunks = [chunk()]
    build_summaries.update_summaries(chunks=chunks)
    settings.SUMMARY_MODEL = "different-answer-model"
    reject_llm(monkeypatch)

    stats = build_summaries.update_summaries(chunks=chunks, changed_book_ids=set())

    assert not stats.written
    assert stats.generated_records == 0
    assert {record.summary_model for record in build_summaries.load_existing(settings.SUMMARIES_JSONL_PATH).values()} == {"extractive"}


@pytest.mark.parametrize("field,value", [("title", "新书名"), ("author", "新作者"), ("chapter_title", "新章节名")])
def test_metadata_change_updates_summary_even_when_source_text_is_unchanged(settings, field, value):
    original = chunk()
    build_summaries.update_summaries(chunks=[original])
    before = build_summaries.load_existing(settings.SUMMARIES_JSONL_PATH)
    updated = original.model_copy(update={field: value})

    stats = build_summaries.update_summaries(chunks=[updated], changed_book_ids={original.book_id})

    after = build_summaries.load_existing(settings.SUMMARIES_JSONL_PATH)
    assert stats.generated_records > 0
    assert after[f"{original.book_id}:chapter:0"].source_hash == before[f"{original.book_id}:chapter:0"].source_hash
    assert getattr(after[f"{original.book_id}:chapter:0"], field) == value


def test_generation_setting_change_can_force_all_books(settings):
    chunks = [chunk("a"), chunk("b")]
    build_summaries.update_summaries(chunks=chunks)
    settings.SUMMARY_CHAPTER_TARGET_CHARS += 100

    stats = build_summaries.update_summaries(chunks=chunks, changed_book_ids=set(), force=True)

    assert stats.generated_records == 4
    assert stats.written


def test_empty_library_creates_summary_file_once_without_llm(settings, monkeypatch):
    reject_llm(monkeypatch)
    first = build_summaries.update_summaries(chunks=[], backend="llm", changed_book_ids=set())
    path = Path(settings.SUMMARIES_JSONL_PATH)
    first_modified = path.stat().st_mtime_ns

    second = build_summaries.update_summaries(chunks=[], backend="llm", changed_book_ids=set())

    assert first.written
    assert first.total_records == 0
    assert not second.written
    assert path.read_text(encoding="utf-8") == ""
    assert path.stat().st_mtime_ns == first_modified


def test_cli_dry_run_limit_does_not_write_or_construct_llm(settings, monkeypatch, capsys):
    chunks = [chunk("a"), chunk("b")]
    Path(settings.CHUNKS_JSONL_PATH).write_text(
        "".join(json.dumps(item.model_dump(), ensure_ascii=False) + "\n" for item in chunks), encoding="utf-8",
    )
    reject_llm(monkeypatch)
    monkeypatch.setattr("sys.argv", ["build_summaries.py", "--dry-run", "--limit", "1", "--backend", "llm"])

    build_summaries.main()

    assert not Path(settings.SUMMARIES_JSONL_PATH).exists()
    assert "需要生成或更新 1 条摘要" in capsys.readouterr().out


def test_limited_generation_preserves_previous_records_for_next_run(settings, monkeypatch):
    chunks = [chunk("a"), chunk("b")]
    fake_llm(monkeypatch)
    first = build_summaries.update_summaries(chunks=chunks, backend="llm", limit=1)
    first_records = build_summaries.load_existing(settings.SUMMARIES_JSONL_PATH)

    second = build_summaries.update_summaries(chunks=chunks, backend="llm", limit=1)
    second_records = build_summaries.load_existing(settings.SUMMARIES_JSONL_PATH)

    assert first.pending_records == 4
    assert second.pending_records == 3
    assert first.generated_records == second.generated_records == 1
    assert len(second_records) == 2
    assert all(second_records[key] == value for key, value in first_records.items())
