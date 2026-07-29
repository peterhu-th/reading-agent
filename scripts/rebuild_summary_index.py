import sys
from pathlib import Path

from langchain_chroma import Chroma
from langchain_core.documents import Document

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.config import get_settings
from app.retrieval.embedding_factory import make_embeddings
from app.retrieval.summary_retriever import load_summaries


def main() -> None:
    settings = get_settings()
    summaries = load_summaries(settings.SUMMARIES_JSONL_PATH)
    if not summaries:
        raise SystemExit("No summaries found. Run: python scripts/build_summaries.py")

    vectorstore = Chroma(
        collection_name=settings.SUMMARY_CHROMA_COLLECTION,
        embedding_function=make_embeddings(),
        persist_directory=settings.VECTOR_DB_PATH,
    )
    ids = [record.summary_id for record in summaries]
    documents = []
    for record in summaries:
        metadata = record.model_dump()
        metadata.pop("text", None)
        metadata["source_chunk_ids"] = ",".join(record.source_chunk_ids)
        documents.append(Document(page_content=record.text, metadata=metadata))

    try:
        vectorstore.delete(ids=ids)
    except Exception:
        pass

    vectorstore.add_documents(documents=documents, ids=ids)
    print(f"Indexed {len(summaries)} summaries into {settings.SUMMARY_CHROMA_COLLECTION}.")


if __name__ == "__main__":
    main()
