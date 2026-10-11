from __future__ import annotations

import argparse
import hashlib
import json
import warnings
from dataclasses import asdict, dataclass
from itertools import groupby
from pathlib import Path
from typing import Any

from bs4 import BeautifulSoup, XMLParsedAsHTMLWarning
from ebooklib import epub

from app.ingestion.epub_source import (
    BookMetadata,
    all_metadata_values,
    clean_book_title,
    extract_html_documents,
    extract_metadata,
    file_sha256,
    first_metadata_value,
    make_book_id,
    normalize_inline_text,
    read_epub_file,
)
from app.ingestion.normalize import is_retrievable_paragraph, normalize_whitespace
from app.ingestion.reader_epub import load_epub_for_reader
from app.models.schemas import BookParagraph, ReaderContentRecord

DEFAULT_MAX_CHARS = 1000

warnings.filterwarnings("ignore", category=XMLParsedAsHTMLWarning)


@dataclass(frozen=True)
class Chapter:
    chapter_index: int
    href: str
    title: str | None
    text: str

@dataclass(frozen=True)
class Chunk:
    chunk_id: str
    book_id: str
    chapter_index: int
    chunk_index: int
    href: str
    text: str

@dataclass(frozen=True)
class LoadedEpub:
    book_id: str
    file_hash: str
    file_path: str
    metadata: BookMetadata
    chapters: list[Chapter]
    chunks: list[Chunk]


def load_epub(epub_path: str | Path) -> list[BookParagraph]:
    """Project reader source records into RAG without renumbering locations."""
    path = Path(epub_path).expanduser().resolve()
    return reader_records_to_paragraphs(load_epub_for_reader(path), str(path))


def reader_records_to_paragraphs(records: list[ReaderContentRecord], source_path: str) -> list[BookParagraph]:
    paragraphs: list[BookParagraph] = []
    # A source paragraph may contain several short <br> lines. Assess the whole
    # source node, then keep each line's reader coordinates in the RAG projection.
    groups = groupby(records, key=lambda record: (record.chapter_index, record.source_href, record.source_node_path))
    for _, group in groups:
        lines = list(group)
        text = normalize_whitespace(" ".join(record.text for record in lines))
        if not is_retrievable_chapter(lines[0].chapter_title) or not is_retrievable_paragraph(text):
            continue
        for record in lines:
            paragraphs.append(
                BookParagraph(
                    book_id=record.book_id,
                    title=record.title,
                    author=record.author,
                    source_path=source_path,
                    chapter_index=record.chapter_index,
                    chapter_title=record.chapter_title,
                    paragraph_index=record.paragraph_index,
                    text=normalize_whitespace(record.text),
                )
            )
    return paragraphs


def extract_chapters(book: epub.EpubBook) -> list[Chapter]:
    """Extract readable chapters in EPUB spine order"""
    documents = extract_html_documents(book)
    chapters: list[Chapter] = []

    for document in documents:
        text, title = html_to_text(document["html"])
        if not text:
            continue
        chapters.append(
            Chapter(
                chapter_index=len(chapters),
                href=document["href"],
                title=title,
                text=text,
            )
        )

    return chapters


def chapters_to_paragraphs(
    book_id: str,
    source_path: str,
    metadata: BookMetadata,
    chapters: list[Chapter],
) -> list[BookParagraph]:
    """Convert parsed chapters into the project's paragraph schema."""
    title = metadata.title or clean_book_title(Path(source_path).stem) or Path(source_path).stem
    author = ", ".join(metadata.authors)
    paragraphs: list[BookParagraph] = []

    output_chapter_index = 0
    for chapter in chapters:
        if not is_retrievable_chapter(chapter.title):
            continue
        chapter_texts: list[str] = []
        for raw_paragraph in normalize_block_text(chapter.text).split("\n"):
            text = normalize_whitespace(raw_paragraph)
            if not is_retrievable_paragraph(text):
                continue
            chapter_texts.append(text)

        if not chapter_texts:
            continue

        for paragraph_index, text in enumerate(chapter_texts):
            paragraphs.append(
                BookParagraph(
                    book_id=book_id,
                    title=title,
                    author=author,
                    source_path=source_path,
                    chapter_index=output_chapter_index,
                    chapter_title=chapter.title or "",
                    paragraph_index=paragraph_index,
                    text=text,
                )
            )
        output_chapter_index += 1

    return paragraphs


def is_retrievable_chapter(title: str | None) -> bool:
    clean = normalize_inline_text(title)
    if not clean:
        return True
    return clean not in {"目录", "版权", "封面", "扉页", "描述"}


def html_to_text(html: bytes | str) -> tuple[str, str | None]:
    """Convert one EPUB HTML document to clean text and an optional title."""
    soup = BeautifulSoup(html, "lxml")

    for tag in soup(["script", "style", "noscript"]):
        tag.decompose()

    title = None
    heading = soup.find(["h1", "h2", "h3", "title"])
    if heading:
        title = normalize_inline_text(heading.get_text(" ", strip=True))

    blocks: list[str] = []
    for node in soup.find_all(["h1", "h2", "h3", "h4", "p", "li", "blockquote"]):
        text = normalize_inline_text(node.get_text(" ", strip=True))
        if text:
            blocks.append(text)

    if blocks:
        return "\n".join(blocks), title

    return normalize_block_text(soup.get_text("\n", strip=True)), title


def chunk_text(text: str, max_chars: int = DEFAULT_MAX_CHARS) -> list[str]:
    """Split text into paragraph-aware chunks."""
    if max_chars <= 0:
        raise ValueError("max_chars must be greater than 0")

    paragraphs = [p for p in normalize_block_text(text).split("\n") if p]
    chunks: list[str] = []
    current: list[str] = []
    current_len = 0

    for paragraph in paragraphs:
        if len(paragraph) > max_chars:
            flush_chunk(chunks, current)
            current = []
            current_len = 0
            chunks.extend(split_long_paragraph(paragraph, max_chars))
            continue

        next_len = current_len + len(paragraph) + (1 if current else 0)
        if current and next_len > max_chars:
            flush_chunk(chunks, current)
            current = [paragraph]
            current_len = len(paragraph)
        else:
            current.append(paragraph)
            current_len = next_len

    flush_chunk(chunks, current)
    return chunks


def build_chunks(
    book_id: str,
    chapters: list[Chapter],
    max_chars: int = DEFAULT_MAX_CHARS,
) -> list[Chunk]:
    """Build chunk records for all chapters."""
    chunks: list[Chunk] = []

    for chapter in chapters:
        chapter_chunks = chunk_text(chapter.text, max_chars=max_chars)
        for chunk_index, text in enumerate(chapter_chunks):
            chunks.append(
                Chunk(
                    chunk_id=make_chunk_id(
                        book_id=book_id,
                        chapter_index=chapter.chapter_index,
                        chunk_index=chunk_index,
                        text=text,
                    ),
                    book_id=book_id,
                    chapter_index=chapter.chapter_index,
                    chunk_index=chunk_index,
                    href=chapter.href,
                    text=text,
                )
            )

    return chunks


def make_chunk_id(
    book_id: str,
    chapter_index: int,
    chunk_index: int,
    text: str,
) -> str:
    """Create a stable chunk ID tied to book, position, and content."""
    text_hash = hashlib.sha256(text.encode("utf-8")).hexdigest()
    raw = f"{book_id}:{chapter_index}:{chunk_index}:{text_hash}".encode("utf-8")
    return hashlib.sha256(raw).hexdigest()


def split_long_paragraph(paragraph: str, max_chars: int) -> list[str]:
    """Split a single oversized paragraph into fixed-size chunks."""
    return [
        paragraph[start : start + max_chars]
        for start in range(0, len(paragraph), max_chars)
    ]


def flush_chunk(chunks: list[str], current: list[str]) -> None:
    """Append a buffered chunk if it contains text."""
    if current:
        chunks.append("\n".join(current))


def normalize_block_text(text: str) -> str:
    """Normalize block text while preserving paragraph boundaries."""
    lines = [normalize_inline_text(line) for line in text.splitlines()]
    lines = [line for line in lines if line]
    return "\n".join(lines)


def loaded_epub_to_dict(loaded: LoadedEpub, include_text: bool = True) -> dict[str, Any]:
    """Convert dataclasses to dictionaries for JSON or downstream storage."""
    data = asdict(loaded)
    if include_text:
        return data

    for chapter in data["chapters"]:
        chapter["text_len"] = len(chapter.pop("text", ""))
    for chunk in data["chunks"]:
        chunk["text_len"] = len(chunk.pop("text", ""))
    return data


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Load and chunk an EPUB file.")
    parser.add_argument("epub_path", help="Path to the .epub file")
    parser.add_argument(
        "--max-chars",
        type=int,
        default=DEFAULT_MAX_CHARS,
        help=f"Maximum characters per chunk. Default: {DEFAULT_MAX_CHARS}",
    )
    parser.add_argument(
        "--json-out",
        help="Optional path for writing the full parsed result as JSON.",
    )
    parser.add_argument(
        "--no-text",
        action="store_true",
        help="Omit chapter/chunk text from JSON output and print only lengths.",
    )
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    paragraphs = load_epub(args.epub_path)

    summary: dict[str, int | str] = {"paragraph_count": len(paragraphs)}
    if paragraphs:
        first = paragraphs[0]
        summary.update(
            {
                "book_id": first.book_id,
                "title": first.title,
                "author": first.author,
            }
        )
    print(json.dumps(summary, ensure_ascii=False, indent=2))

    if args.json_out:
        output_path = Path(args.json_out).expanduser().resolve()
        output_path.parent.mkdir(parents=True, exist_ok=True)
        data = [paragraph.model_dump() for paragraph in paragraphs]
        output_path.write_text(
            json.dumps(data, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )


if __name__ == "__main__":
    main()

