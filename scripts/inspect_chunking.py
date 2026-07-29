import argparse
import json
import random
import sys
from collections import Counter
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.config import get_settings
from app.models.schemas import TextChunk


if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")


def load_jsonl(path: str) -> list[TextChunk]:
    input_path = Path(path)
    if not input_path.exists():
        return []

    chunks: list[TextChunk] = []
    with input_path.open("r", encoding="utf-8") as file:
        for line in file:
            line = line.strip()
            if line:
                chunks.append(TextChunk(**json.loads(line)))
    return chunks


def main() -> None:
    parser = argparse.ArgumentParser(description="Inspect chunking output without dumping too much text.")
    parser.add_argument("--sample", type=int, default=3, help="Number of random chunks to print.")
    parser.add_argument("--chars", type=int, default=240, help="Maximum preview chars per sample.")
    parser.add_argument("--seed", type=int, default=7, help="Random seed for stable samples.")
    parser.add_argument("--show-id", action="store_true", help="Show internal chunk IDs.")
    parser.add_argument("--full", action="store_true", help="Print full chunk text instead of a preview.")
    args = parser.parse_args()

    settings = get_settings()
    chunks = load_jsonl(settings.CHUNKS_JSONL_PATH)
    if not chunks:
        print(f"No chunks found at {settings.CHUNKS_JSONL_PATH}")
        return

    title_counts = Counter(chunk.title for chunk in chunks)
    avg_chars = sum(len(chunk.text) for chunk in chunks) / len(chunks)
    print(
        f"chunks={len(chunks)}, books={len(title_counts)}, "
        f"avg_chars={avg_chars:.0f}, path={settings.CHUNKS_JSONL_PATH}"
    )
    print("Top books by chunk count:")
    for title, count in title_counts.most_common(8):
        print(f"- {title}: {count}")

    rng = random.Random(args.seed)
    sample = rng.sample(chunks, min(max(args.sample, 0), len(chunks)))
    if not sample:
        return

    print("\nSamples:")
    for index, chunk in enumerate(sample, start=1):
        chapter = chunk.chapter_title or f"第 {chunk.chapter_index} 章"
        text = " ".join(chunk.text.split())
        print("-" * 72)
        print(f"[{index}] {chunk.title} / {chunk.author or 'unknown'} / {chapter}")
        print(
            f"paragraphs={chunk.start_paragraph_index}-{chunk.end_paragraph_index}, "
            f"chunk_chars={len(text)}"
        )
        if args.show_id:
            print(f"chunk_id={chunk.chunk_id}")

        if args.full or len(text) <= args.chars:
            print(text)
        else:
            print(text[: args.chars])
            print(f"...（仅预览前 {args.chars} 字，完整 chunk 为 {len(text)} 字；使用 --full 查看全文）")


if __name__ == "__main__":
    main()
