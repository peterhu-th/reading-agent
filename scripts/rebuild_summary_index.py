import sys
from pathlib import Path

from langchain_core.documents import Document

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.config import get_settings
from app.ingestion.build_index import sync_index_documents
from app.retrieval.summary_retriever import load_summaries


def main() -> None:
    settings = get_settings()
    if not Path(settings.SUMMARIES_JSONL_PATH).exists():
        raise SystemExit("Summary file not found. Run: python scripts/build_summaries.py")
    summaries = load_summaries(settings.SUMMARIES_JSONL_PATH)
    ids = [record.summary_id for record in summaries]
    documents = []
    for record in summaries:
        metadata = record.model_dump()
        metadata.pop("text", None)
        metadata["source_chunk_ids"] = ",".join(record.source_chunk_ids)
        documents.append(Document(page_content=record.text, metadata=metadata))

    stats = sync_index_documents(
        documents,
        ids,
        settings.SUMMARY_CHROMA_COLLECTION,
        settings.VECTOR_DB_PATH,
    )
    print(
        f"Indexed {len(summaries)} summaries into {settings.SUMMARY_CHROMA_COLLECTION}; "
        f"deleted {stats.deleted_chunks} stale summaries."
    )


if __name__ == "__main__":
    main()
