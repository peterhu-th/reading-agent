import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.ingestion.build_index import rebuild_index_with_stats


def main() -> None:
    started = time.perf_counter()
    print("Rebuilding Chroma chunk index...")
    stats = rebuild_index_with_stats(progress=print)
    elapsed = time.perf_counter() - started
    reset_text = " yes" if stats.reset_collection else " no"
    print(
        "Done. "
        f"total={stats.total_chunks}, indexed={stats.indexed_chunks}, "
        f"skipped={stats.skipped_chunks}, deleted={stats.deleted_chunks}, "
        f"reset={reset_text}, elapsed={elapsed:.1f}s"
    )


if __name__ == "__main__":
    main()
