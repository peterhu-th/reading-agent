import argparse
import sys
from pathlib import Path

from langchain_core.documents import Document

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.config import get_settings
from app.ingestion.build_index import IndexBuildStats, ProgressCallback, sync_index_documents
from app.retrieval.summary_retriever import load_summaries


def rebuild_summary_index(
    progress: ProgressCallback | None = None,
    *,
    force_reset: bool = False,
) -> IndexBuildStats:
    settings = get_settings()
    if not Path(settings.SUMMARIES_JSONL_PATH).exists():
        raise FileNotFoundError("Summary file not found. Run: python scripts/build_summaries.py")
    summaries = load_summaries(settings.SUMMARIES_JSONL_PATH)
    ids = [record.summary_id for record in summaries]
    documents = []
    for record in summaries:
        metadata = record.model_dump(exclude={"text"}, exclude_none=True)
        metadata["source_chunk_ids"] = ",".join(record.source_chunk_ids)
        documents.append(Document(page_content=record.text, metadata=metadata))

    return sync_index_documents(
        documents,
        ids,
        settings.SUMMARY_CHROMA_COLLECTION,
        settings.VECTOR_DB_PATH,
        progress,
        reuse_unchanged=True,
        compare_text=True,
        force_reset=force_reset,
    )


def main(*, verbose: bool = False) -> None:
    try:
        stats = rebuild_summary_index(progress=print if verbose else None)
    except FileNotFoundError as exc:
        raise SystemExit(str(exc)) from exc
    print(
        f"Summary index: total={stats.total_chunks}, indexed={stats.indexed_chunks}, "
        f"skipped={stats.skipped_chunks}, deleted={stats.deleted_chunks}."
    )


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--verbose", action="store_true", help="Print detailed indexing progress.")
    main(verbose=parser.parse_args().verbose)
