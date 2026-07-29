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
) -> IndexBuildStats:
    settings = get_settings()
    chunks = load_chunks(chunks_path or settings.CHUNKS_JSONL_PATH)
    if not chunks:
        return IndexBuildStats(total_chunks=0, indexed_chunks=0, skipped_chunks=0, deleted_chunks=0)

    ids = [chunk.chunk_id for chunk in chunks]
    documents = [chunk_to_document(chunk) for chunk in chunks]
    vectorstore = make_vectorstore(settings.CHROMA_COLLECTION, settings.VECTOR_DB_PATH)

    existing_hashes = load_existing_hashes(vectorstore, progress)
    current_ids = set(ids)
    stale_ids = [chunk_id for chunk_id in existing_hashes if chunk_id not in current_ids]
    if stale_ids:
        emit(progress, f"Deleting {len(stale_ids)} stale chunks from Chroma...")
        vectorstore.delete(ids=stale_ids)

    changed_documents: list[Document] = []
    changed_ids: list[str] = []
    for chunk_id, document in zip(ids, documents, strict=True):
        if existing_hashes.get(chunk_id) == document.metadata.get("content_hash"):
            continue
        changed_ids.append(chunk_id)
        changed_documents.append(document)

    skipped = len(documents) - len(changed_documents)
    emit(
        progress,
        f"Chunks: total={len(documents)}, changed={len(changed_documents)}, skipped={skipped}.",
    )

    try:
        add_documents_in_batches(vectorstore, changed_documents, changed_ids, progress)
    except Exception as exc:
        if "expecting embedding with dimension" not in str(exc):
            raise
        emit(progress, "Embedding dimension changed; resetting Chroma collection...")
        reset_collection(vectorstore, settings.CHROMA_COLLECTION)
        vectorstore = make_vectorstore(settings.CHROMA_COLLECTION, settings.VECTOR_DB_PATH)
        add_documents_in_batches(vectorstore, documents, ids, progress)
        return IndexBuildStats(
            total_chunks=len(documents),
            indexed_chunks=len(documents),
            skipped_chunks=0,
            deleted_chunks=len(stale_ids),
            reset_collection=True,
        )

    return IndexBuildStats(
        total_chunks=len(documents),
        indexed_chunks=len(changed_documents),
        skipped_chunks=skipped,
        deleted_chunks=len(stale_ids),
    )


def make_vectorstore(collection_name: str, persist_directory: str) -> Chroma:
    return Chroma(
        collection_name=collection_name,
        embedding_function=make_embeddings(),
        persist_directory=persist_directory,
    )


def load_existing_hashes(
    vectorstore: Chroma,
    progress: ProgressCallback | None = None,
) -> dict[str, str]:
    emit(progress, "Reading existing Chroma metadata...")
    try:
        raw = vectorstore.get(include=["metadatas"])
    except Exception:
        return {}

    ids = raw.get("ids") or []
    metadatas = raw.get("metadatas") or []
    hashes: dict[str, str] = {}
    for chunk_id, metadata in zip(ids, metadatas, strict=False):
        if isinstance(metadata, dict):
            content_hash = metadata.get("content_hash")
            if isinstance(content_hash, str) and content_hash:
                hashes[str(chunk_id)] = content_hash
    return hashes


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
        emit(progress, f"Indexing chunks {start + 1}-{end}/{total}...")
        vectorstore.add_documents(
            documents=documents[start:end],
            ids=ids[start:end],
        )


def reset_collection(vectorstore: Chroma, collection_name: str) -> None:
    try:
        vectorstore._client.delete_collection(collection_name)
    except Exception:
        pass


def emit(progress: ProgressCallback | None, message: str) -> None:
    if progress:
        progress(message)
