import json
from pathlib import Path

import pytest
from langchain_core.documents import Document
from langchain_core.embeddings import Embeddings

from app.config import Settings
from app.ingestion import build_index
from app.models.schemas import SummaryRecord, TextChunk
from scripts import rebuild_summary_index


class RecordingEmbeddings(Embeddings):
    def __init__(self) -> None:
        self.dimension = 2
        self.value = 1.0
        self.batches: list[list[str]] = []

    def embed_documents(self, texts: list[str]) -> list[list[float]]:
        self.batches.append(texts)
        return [[self.value] * self.dimension for _ in texts]

    def embed_query(self, text: str) -> list[float]:
        return [self.value] * self.dimension


@pytest.fixture
def index_environment(tmp_path, monkeypatch):
    settings = Settings(
        OPENAI_API_KEY="test",
        OPENAI_BASE_URL="http://127.0.0.1:1/v1",
        EMBEDDING_BACKEND="sentence-transformers",
        EMBEDDING_MODEL="test-model-a",
        VECTOR_DB_PATH=str(tmp_path / "chroma"),
        CHUNKS_JSONL_PATH=str(tmp_path / "chunks.jsonl"),
        SUMMARIES_JSONL_PATH=str(tmp_path / "summaries.jsonl"),
    )
    embeddings = RecordingEmbeddings()
    monkeypatch.setattr(build_index, "get_settings", lambda: settings)
    monkeypatch.setattr(build_index, "make_embeddings", lambda: embeddings)
    monkeypatch.setattr(rebuild_summary_index, "get_settings", lambda: settings)
    return settings, embeddings


def write_records(path, records) -> None:
    Path(path).write_text(
        "".join(json.dumps(record.model_dump(), ensure_ascii=False) + "\n" for record in records),
        encoding="utf-8",
    )


def chunk() -> TextChunk:
    return TextChunk(
        chunk_id="book:0:0",
        book_id="book",
        title="Test book",
        chapter_index=0,
        chunk_index=0,
        start_paragraph_index=0,
        end_paragraph_index=0,
        text="Unchanged source text.",
    )


def collection(settings, *, summary=False):
    return build_index.make_vectorstore(
        settings.SUMMARY_CHROMA_COLLECTION if summary else settings.CHROMA_COLLECTION,
        settings.VECTOR_DB_PATH,
        with_embeddings=False,
    )


def test_unchanged_chunks_reuse_vectors_with_matching_model(index_environment):
    settings, embeddings = index_environment
    write_records(settings.CHUNKS_JSONL_PATH, [chunk()])
    build_index.rebuild_index_with_stats()
    embeddings.batches.clear()

    stats = build_index.rebuild_index_with_stats()

    assert stats.indexed_chunks == 0
    assert stats.skipped_chunks == 1
    assert not stats.reset_collection
    assert embeddings.batches == []
    assert collection(settings)._collection.metadata["embedding_model"] == "test-model-a"


def test_requested_reset_reembeds_unchanged_chunks(index_environment):
    settings, embeddings = index_environment
    write_records(settings.CHUNKS_JSONL_PATH, [chunk()])
    build_index.rebuild_index_with_stats()
    embeddings.batches.clear()
    old_collection_id = collection(settings)._collection.id

    stats = build_index.rebuild_index_with_stats(force_reset=True)

    assert stats.reset_collection
    assert stats.indexed_chunks == 1
    assert stats.skipped_chunks == 0
    assert embeddings.batches == [[chunk().text]]
    store = collection(settings)
    assert store._collection.id != old_collection_id
    assert store.get()["ids"] == [chunk().chunk_id]


@pytest.mark.parametrize("dimension", [2, 3])
def test_model_change_reembeds_unchanged_text_even_with_same_dimension(index_environment, dimension):
    settings, embeddings = index_environment
    write_records(settings.CHUNKS_JSONL_PATH, [chunk()])
    build_index.rebuild_index_with_stats()
    embeddings.batches.clear()
    settings.EMBEDDING_MODEL = "test-model-b"
    embeddings.dimension = dimension
    embeddings.value = 2.0

    stats = build_index.rebuild_index_with_stats()

    assert stats.indexed_chunks == 1
    assert stats.skipped_chunks == 0
    assert stats.reset_collection
    assert embeddings.batches == [[chunk().text]]
    store = collection(settings)
    assert list(store.get(include=["embeddings"])["embeddings"][0]) == [2.0] * dimension
    assert store._collection.metadata["embedding_model"] == "test-model-b"


def test_legacy_collection_without_model_configuration_is_reembedded(index_environment):
    settings, embeddings = index_environment
    write_records(settings.CHUNKS_JSONL_PATH, [chunk()])
    legacy = build_index.make_vectorstore(settings.CHROMA_COLLECTION, settings.VECTOR_DB_PATH)
    legacy.add_documents([build_index.chunk_to_document(chunk())], ids=[chunk().chunk_id])
    embeddings.batches.clear()

    stats = build_index.rebuild_index_with_stats()

    assert stats.reset_collection
    assert stats.indexed_chunks == 1
    assert embeddings.batches == [[chunk().text]]
    assert collection(settings)._collection.metadata == build_index.embedding_index_config()


def test_stale_chunk_without_content_hash_is_deleted(index_environment):
    settings, _ = index_environment
    write_records(settings.CHUNKS_JSONL_PATH, [chunk()])
    build_index.rebuild_index_with_stats()
    store = build_index.make_vectorstore(settings.CHROMA_COLLECTION, settings.VECTOR_DB_PATH)
    store.add_documents([Document(page_content="Deleted source", metadata={"title": "Old"})], ids=["old"])

    stats = build_index.rebuild_index_with_stats()

    assert stats.deleted_chunks == 1
    assert collection(settings).get()["ids"] == [chunk().chunk_id]


def test_empty_chunk_file_clears_existing_collection_without_embedding(index_environment):
    settings, embeddings = index_environment
    write_records(settings.CHUNKS_JSONL_PATH, [chunk()])
    build_index.rebuild_index_with_stats()
    write_records(settings.CHUNKS_JSONL_PATH, [])
    embeddings.batches.clear()

    stats = build_index.rebuild_index_with_stats()

    assert stats.total_chunks == 0
    assert stats.deleted_chunks == 1
    assert collection(settings).get()["ids"] == []
    assert embeddings.batches == []


def summary(summary_id: str) -> SummaryRecord:
    return SummaryRecord(
        summary_id=summary_id,
        summary_type="chapter",
        book_id="book",
        title="Test book",
        chapter_index=0,
        source_chunk_ids=[chunk().chunk_id],
        source_hash="existing-source-marker",
        summary_model="test",
        prompt_version="test",
        text="Summary text.",
    )


def test_summary_rebuild_deletes_removed_ids_and_clears_empty_inventory(index_environment):
    settings, embeddings = index_environment
    write_records(settings.SUMMARIES_JSONL_PATH, [summary("keep"), summary("deleted")])
    rebuild_summary_index.main()
    write_records(settings.SUMMARIES_JSONL_PATH, [summary("keep")])

    rebuild_summary_index.main()

    assert collection(settings, summary=True).get()["ids"] == ["keep"]
    write_records(settings.SUMMARIES_JSONL_PATH, [])
    embeddings.batches.clear()
    rebuild_summary_index.main()
    assert collection(settings, summary=True).get()["ids"] == []
    assert embeddings.batches == []


def test_summary_rebuild_migrates_embedding_dimension(index_environment):
    settings, embeddings = index_environment
    write_records(settings.SUMMARIES_JSONL_PATH, [summary("keep")])
    rebuild_summary_index.main()
    settings.EMBEDDING_MODEL = "test-model-b"
    embeddings.dimension = 3

    rebuild_summary_index.main()

    store = collection(settings, summary=True)
    assert len(store.get(include=["embeddings"])["embeddings"][0]) == 3
    assert store._collection.metadata["embedding_model"] == "test-model-b"


def test_missing_summary_file_does_not_clear_existing_collection(index_environment):
    settings, _ = index_environment
    write_records(settings.SUMMARIES_JSONL_PATH, [summary("keep")])
    rebuild_summary_index.main()
    Path(settings.SUMMARIES_JSONL_PATH).unlink()

    with pytest.raises(SystemExit, match="Summary file not found"):
        rebuild_summary_index.main()

    assert collection(settings, summary=True).get()["ids"] == ["keep"]


def test_unchanged_book_summary_reuses_embedding_without_output(index_environment, capsys):
    settings, embeddings = index_environment
    record = summary("book").model_copy(update={"summary_type": "book", "chapter_index": None})
    write_records(settings.SUMMARIES_JSONL_PATH, [record])
    rebuild_summary_index.rebuild_summary_index()
    embeddings.batches.clear()

    stats = rebuild_summary_index.rebuild_summary_index()

    assert stats.indexed_chunks == 0
    assert stats.skipped_chunks == 1
    assert embeddings.batches == []
    assert capsys.readouterr().out == ""


@pytest.mark.parametrize("changes", [{"text": "Edited summary text."}, {"title": "Edited title"}])
def test_only_changed_summary_text_or_metadata_is_reembedded(index_environment, changes):
    settings, embeddings = index_environment
    unchanged = summary("unchanged")
    changed = summary("changed")
    write_records(settings.SUMMARIES_JSONL_PATH, [unchanged, changed])
    rebuild_summary_index.rebuild_summary_index()
    embeddings.batches.clear()
    updated = changed.model_copy(update=changes)
    write_records(settings.SUMMARIES_JSONL_PATH, [unchanged, updated])

    stats = rebuild_summary_index.rebuild_summary_index()

    assert stats.indexed_chunks == stats.skipped_chunks == 1
    assert embeddings.batches == [[updated.text]]
