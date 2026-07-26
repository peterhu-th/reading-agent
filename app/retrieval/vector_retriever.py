from typing import Any

from langchain_chroma import Chroma

from app.config import get_settings
from app.models.schemas import RetrievedChunk, TextChunk
from app.retrieval.embedding_factory import make_embeddings


class VectorRetriever:
    """Retrieve chunks from the persisted Chroma index."""

    def __init__(self) -> None:
        settings = get_settings()
        self.vectorstore = Chroma(
            collection_name=settings.CHROMA_COLLECTION,
            embedding_function=make_embeddings(),
            persist_directory=settings.VECTOR_DB_PATH,
        )

    def search(
        self,
        query: str,
        top_k: int = 5,
        metadata_filter: dict[str, str | list[str]] | None = None,
    ) -> list[RetrievedChunk]:
        query = query.strip()
        if not query:
            return []

        where = chroma_where(metadata_filter)
        if where:
            results = self.vectorstore.similarity_search_with_score(
                query,
                k=top_k,
                filter=where,
            )
        else:
            results = self.vectorstore.similarity_search_with_score(query, k=top_k)
        retrieved: list[RetrievedChunk] = []
        for document, score in results:
            data = dict(document.metadata)
            data["text"] = document.page_content
            retrieved.append(
                RetrievedChunk(
                    chunk=TextChunk(**data),
                    score=score,
                    vector_score=score,
                    matched_queries=[query],
                )
            )
        return retrieved


def chroma_where(metadata_filter: dict[str, str | list[str]] | None) -> dict[str, Any] | None:
    if not metadata_filter:
        return None

    conditions: list[dict[str, Any]] = []
    for key, value in metadata_filter.items():
        if isinstance(value, list):
            values = [item for item in value if item]
            if len(values) == 1:
                conditions.append({key: values[0]})
            elif len(values) > 1:
                conditions.append({"$or": [{key: item} for item in values]})
        elif value:
            conditions.append({key: value})

    if not conditions:
        return None
    if len(conditions) == 1:
        return conditions[0]
    return {"$and": conditions}
