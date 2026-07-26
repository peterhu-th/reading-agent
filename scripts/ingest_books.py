import json
import sys
from pathlib import Path

from pydantic import BaseModel

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.config import get_settings
from app.ingestion.chunking import chunk_paragraphs
from app.ingestion.load_epub import load_epub
from app.models.schemas import BookParagraph


def write_jsonl(path: str, records: list[BaseModel]) -> None:
    output_path = Path(path)
    output_path.parent.mkdir(parents=True, exist_ok=True)
    with output_path.open("w", encoding="utf-8") as file:
        for record in records:
            file.write(json.dumps(record.model_dump(), ensure_ascii=False) + "\n")


def main() -> None:
    settings = get_settings()
    raw_dir = Path(settings.RAW_EPUB_DIR)
    epub_paths = sorted(raw_dir.glob("*.epub"))

    all_paragraphs: list[BookParagraph] = []
    for epub_path in epub_paths:
        paragraphs = load_epub(epub_path)
        all_paragraphs.extend(paragraphs)
        print(f"Loaded {len(paragraphs)} paragraphs from {epub_path.name}")

    chunks = chunk_paragraphs(
        all_paragraphs,
        chunk_size=settings.CHUNK_SIZE,
        overlap=settings.CHUNK_OVERLAP,
    )
    write_jsonl(settings.BOOKS_JSONL_PATH, all_paragraphs)
    write_jsonl(settings.CHUNKS_JSONL_PATH, chunks)

    print(
        f"Ingested {len(epub_paths)} EPUB files, "
        f"{len(all_paragraphs)} paragraphs, {len(chunks)} chunks"
    )


if __name__ == "__main__":
    main()
