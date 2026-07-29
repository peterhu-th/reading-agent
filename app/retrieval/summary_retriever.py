import json
from pathlib import Path
from typing import Any

from langchain_chroma import Chroma

from app.config import get_settings
from app.models.schemas import SummaryRecord
from app.retrieval.embedding_factory import make_embeddings
from app.retrieval.vector_retriever import chroma_where


class SummaryRetriever:
    """Retrieve offline-generated chapter/book summaries."""

    def __init__(self) -> None:
        settings = get_settings()
        self.settings = settings
        self.vectorstore = Chroma(
            collection_name=settings.SUMMARY_CHROMA_COLLECTION,
            embedding_function=make_embeddings(),
            persist_directory=settings.VECTOR_DB_PATH,
        )

    def search(
        self,
        query: str,
        top_k: int = 5,
        metadata_filter: dict[str, str | list[str]] | None = None,
    ) -> list[SummaryRecord]:
        if not Path(self.settings.SUMMARIES_JSONL_PATH).exists():
            return []

        where = chroma_where(metadata_filter)
        try:
            if where:
                results = self.vectorstore.similarity_search_with_score(query, k=top_k, filter=where)
            else:
                results = self.vectorstore.similarity_search_with_score(query, k=top_k)
        except Exception:
            return []

        summaries: list[SummaryRecord] = []
        for document, _score in results:
            metadata: dict[str, Any] = dict(document.metadata)
            source_chunk_ids = metadata.get("source_chunk_ids", "")
            if isinstance(source_chunk_ids, str):
                metadata["source_chunk_ids"] = [
                    item for item in source_chunk_ids.split(",") if item
                ]
            metadata["text"] = document.page_content
            try:
                summaries.append(SummaryRecord(**metadata))
            except Exception:
                continue
        return summaries


def load_summaries(path: str) -> list[SummaryRecord]:
    input_path = Path(path)
    if not input_path.exists():
        return []

    records: list[SummaryRecord] = []
    with input_path.open("r", encoding="utf-8") as file:
        for line in file:
            line = line.strip()
            if line:
                records.append(SummaryRecord(**json.loads(line)))
    return records
