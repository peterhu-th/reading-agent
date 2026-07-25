import json
from pathlib import Path

from langchain_chroma import Chroma
from langchain_core.documents import Document

from app.config import get_settings
from app.models.schemas import TextChunk
from app.retrieval.embedding_factory import make_embeddings

CHROMA_BATCH_SIZE = 5000


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


def chunk_to_document(chunk: TextChunk) -> Document:
    metadata = chunk.model_dump()
    metadata.pop("text", None)
    return Document(page_content=chunk.text, metadata=metadata)


def rebuild_index(chunks_path: str | None = None) -> int:
    settings = get_settings()
    chunks = load_chunks(chunks_path or settings.CHUNKS_JSONL_PATH)
    if not chunks:
        return 0

    vectorstore = Chroma(
        collection_name=settings.CHROMA_COLLECTION,
        embedding_function=make_embeddings(),
        persist_directory=settings.VECTOR_DB_PATH,
    )

    ids = [chunk.chunk_id for chunk in chunks]
    documents = [chunk_to_document(chunk) for chunk in chunks]

    # Delete known IDs first so repeated rebuilds update content cleanly.
    try:
        vectorstore.delete(ids=ids)
    except Exception:
        pass

    for start in range(0, len(documents), CHROMA_BATCH_SIZE):
        end = start + CHROMA_BATCH_SIZE
        vectorstore.add_documents(
            documents=documents[start:end],
            ids=ids[start:end],
        )
    return len(chunks)
