from langchain_core.embeddings import Embeddings

from app.config import get_settings
from app.retrieval.local_embeddings import LocalHashEmbeddings
from app.retrieval.sentence_transformer_embeddings import SentenceTransformerEmbeddings


def make_embeddings() -> Embeddings:
    settings = get_settings()
    backend = settings.EMBEDDING_BACKEND.strip().lower()

    if backend == "local-hash":
        return LocalHashEmbeddings()

    if backend in {"sentence-transformers", "sentence_transformers"}:
        return SentenceTransformerEmbeddings(
            model_name=settings.EMBEDDING_MODEL,
            device=settings.EMBEDDING_DEVICE,
            batch_size=settings.EMBEDDING_BATCH_SIZE,
            local_files_only=settings.EMBEDDING_LOCAL_FILES_ONLY,
        )

    raise ValueError(f"Unsupported EMBEDDING_BACKEND: {settings.EMBEDDING_BACKEND}")
