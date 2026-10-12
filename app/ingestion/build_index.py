import hashlib
import json
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path

from langchain_chroma import Chroma
from langchain_core.documents import Document

from app.config import get_settings
from app.models.schemas import TextChunk
from app.retrieval.embedding_factory import make_embeddings

CHROMA_BATCH_SIZE = 500


@dataclass(frozen=True)
class IndexBuildStats:
    total_chunks: int
    indexed_chunks: int
    skipped_chunks: int
    deleted_chunks: int
    reset_collection: bool = False


ProgressCallback = Callable[[str], None]


def load_chunks(path: str) -> list[TextChunk]:
    input_path = Path(path)
    if not input_path.exists():
        raise FileNotFoundError(f"chunks JSONL does not exist: {input_path}")

    chunks: list[TextChunk] = []
    with input_path.open("r", encoding="utf-8") as file:
        for line in file:
            line = line.strip()
            if not line:
                continue
            chunks.append(TextChunk(**json.loads(line)))
    return chunks


def chunk_content_hash(chunk: TextChunk) -> str:
    payload = {
        "chunk_id": chunk.chunk_id,
        "book_id": chunk.book_id,
        "title": chunk.title,
        "author": chunk.author,
        "book_type": chunk.book_type,
        "chapter_index": chunk.chapter_index,
        "chapter_title": chunk.chapter_title,
        "chunk_index": chunk.chunk_index,
        "start_paragraph_index": chunk.start_paragraph_index,
        "end_paragraph_index": chunk.end_paragraph_index,
        "text": chunk.text,
    }
    encoded = json.dumps(payload, ensure_ascii=False, sort_keys=True).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def chunk_to_document(chunk: TextChunk) -> Document:
    metadata = chunk.model_dump()
    metadata.pop("text", None)
    metadata["content_hash"] = chunk_content_hash(chunk)
    return Document(page_content=chunk.text, metadata=metadata)


def rebuild_index(chunks_path: str | None = None) -> int:
    """Backward-compatible wrapper returning the number of available chunks."""
    return rebuild_index_with_stats(chunks_path).total_chunks


def rebuild_index_with_stats(
    chunks_path: str | None = None,
    progress: ProgressCallback | None = None,
    *,
    force_reset: bool = False,
) -> IndexBuildStats:
    settings = get_settings()
    chunks = load_chunks(chunks_path or settings.CHUNKS_JSONL_PATH)
    ids = [chunk.chunk_id for chunk in chunks]
    documents = [chunk_to_document(chunk) for chunk in chunks]
    return sync_index_documents(
        documents,
        ids,
        settings.CHROMA_COLLECTION,
        settings.VECTOR_DB_PATH,
        progress,
        reuse_unchanged=True,
        force_reset=force_reset,
    )


def embedding_index_config() -> dict[str, str | bool]:
    """Describe the embedding space independently of batching and hardware."""
    settings = get_settings()
    backend = settings.EMBEDDING_BACKEND.strip().lower().replace("_", "-")
    return {
        "embedding_backend": backend,
        "embedding_model": "local-hash" if backend == "local-hash" else settings.EMBEDDING_MODEL,
        "embedding_normalize": True,
    }


def sync_index_documents(
    documents: list[Document],
    ids: list[str],
    collection_name: str,
    persist_directory: str,
    progress: ProgressCallback | None = None,
    *,
    reuse_unchanged: bool = False,
    compare_text: bool = False,
    force_reset: bool = False,
) -> IndexBuildStats:
    """Synchronize one complete source inventory with its embedding collection.

    Chunk metadata includes the existing content_hash. Summaries additionally
    compare stored text so unchanged summaries can reuse their embeddings.
    """
    vectorstore = make_vectorstore(
        collection_name,
        persist_directory,
        with_embeddings=False,
    )
    emit(progress, "Reading existing Chroma metadata...")
    raw = vectorstore.get(include=["metadatas", "documents"] if compare_text else ["metadatas"])
    existing = dict(zip(raw["ids"], raw["metadatas"], strict=True))
    existing_texts = dict(zip(raw["ids"], raw["documents"], strict=True)) if compare_text else {}
    current_ids = set(ids)
    stale_ids = [document_id for document_id in existing if document_id not in current_ids]

    expected_config = embedding_index_config()
    collection_metadata = vectorstore._collection.metadata or {}
    reset = force_reset or any(
        collection_metadata.get(key) != value for key, value in expected_config.items()
    )
    if reset:
        reason = "Index reset requested" if force_reset else "Embedding configuration changed or missing"
        emit(progress, f"{reason}; rebuilding Chroma collection...")
        vectorstore.reset_collection()
    elif stale_ids:
        emit(progress, f"Deleting {len(stale_ids)} stale records from Chroma...")
        vectorstore.delete(ids=stale_ids)

    changed_documents: list[Document] = []
    changed_ids: list[str] = []
    for chunk_id, document in zip(ids, documents, strict=True):
        if (
            reuse_unchanged
            and not reset
            and existing.get(chunk_id) == document.metadata
            and (not compare_text or existing_texts.get(chunk_id) == document.page_content)
        ):
            continue
        changed_ids.append(chunk_id)
        changed_documents.append(document)

    skipped = len(documents) - len(changed_documents)
    emit(
        progress,
        f"Records: total={len(documents)}, changed={len(changed_documents)}, skipped={skipped}.",
    )

    if changed_documents:
        vectorstore = make_vectorstore(collection_name, persist_directory)
        add_documents_in_batches(vectorstore, changed_documents, changed_ids, progress)
    else:
        emit(progress, "No changed records to index.")

    # Mark the embedding space only after a complete successful rebuild.
    if reset:
        vectorstore._collection.modify(metadata={**collection_metadata, **expected_config})

    return IndexBuildStats(
        total_chunks=len(documents),
        indexed_chunks=len(changed_documents),
        skipped_chunks=skipped,
        deleted_chunks=len(stale_ids),
        reset_collection=reset,
    )


def make_vectorstore(
    collection_name: str,
    persist_directory: str,
    *,
    with_embeddings: bool = True,
) -> Chroma:
    return Chroma(
        collection_name=collection_name,
        embedding_function=make_embeddings() if with_embeddings else None,
        persist_directory=persist_directory,
    )


def add_documents_in_batches(
    vectorstore: Chroma,
    documents: list[Document],
    ids: list[str],
    progress: ProgressCallback | None = None,
) -> None:
    if not documents:
        emit(progress, "No changed chunks to index.")
        return

    total = len(documents)
    for start in range(0, total, CHROMA_BATCH_SIZE):
        end = min(start + CHROMA_BATCH_SIZE, total)
        emit(progress, f"Indexing records {start + 1}-{end}/{total}...")
        vectorstore.add_documents(
            documents=documents[start:end],
            ids=ids[start:end],
        )


def emit(progress: ProgressCallback | None, message: str) -> None:
    if progress:
        progress(message)
