from __future__ import annotations


import argparse
import hashlib
import json
import re
import warnings
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any

from bs4 import BeautifulSoup, XMLParsedAsHTMLWarning
from ebooklib import ITEM_DOCUMENT, epub

from app.ingestion.normalize import is_retrievable_paragraph, normalize_whitespace
from app.models.schemas import BookParagraph

DEFAULT_MAX_CHARS = 1000
HASH_BLOCK_SIZE = 1024 * 1024
TITLE_SUFFIX_STARTERS = ("（", "(", "【", "[")
TITLE_PREFIX_PATTERN = re.compile(r"^(?:【[^】]{1,20}】|\[[^\]]{1,20}\])\s*")

warnings.filterwarnings("ignore", category=XMLParsedAsHTMLWarning)


@dataclass(frozen=True)
class BookMetadata:
    title: str | None = None
    authors: tuple[str, ...] = ()
    identifier: str | None = None
    language: str | None = None
    publisher: str | None = None
    description: str | None = None

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
    """Read an EPUB and return normalized paragraph records."""
    path = Path(epub_path).expanduser().resolve()
    book = read_epub_file(path)
    metadata = extract_metadata(book)
    file_hash = file_sha256(path)
    book_id = make_book_id(metadata, file_hash)
    chapters = extract_chapters(book)
    return chapters_to_paragraphs(
        book_id=book_id,
        source_path=str(path),
        metadata=metadata,
        chapters=chapters,
    )


def read_epub_file(epub_path: Path) -> epub.EpubBook:
    """Read an EPUB file and return EbookLib's book object"""
    if not epub_path.exists():
        raise FileNotFoundError(f"EPUB file does not exist: {epub_path}")
    if not epub_path.is_file():
        raise ValueError(f"EPUB path is not a file: {epub_path}")
    return epub.read_epub(str(epub_path))


def extract_metadata(book: epub.EpubBook) -> BookMetadata:
    """Extract useful Dublin Core metadata from an EPUB book."""
    return BookMetadata(
        title=clean_book_title(first_metadata_value(book, "DC", "title")),
        authors=tuple(all_metadata_values(book, "DC", "creator")),
        identifier=first_metadata_value(book, "DC", "identifier"),
        language=first_metadata_value(book, "DC", "language"),
        publisher=first_metadata_value(book, "DC", "publisher"),
        description=first_metadata_value(book, "DC", "description"),
    )


def first_metadata_value(book: epub.EpubBook, namespace: str, name: str) -> str | None:
    """Return the first non-empty metadata value."""
    values = all_metadata_values(book, namespace, name)
    return values[0] if values else None


def all_metadata_values(book: epub.EpubBook, namespace: str, name: str) -> list[str]:
    """Return all non-empty metadata values for a field."""
    results: list[str] = []
    for value, _attrs in book.get_metadata(namespace, name):
        clean = normalize_inline_text(value)
        if clean:
            results.append(clean)
    return results


def clean_book_title(title: str | None) -> str | None:
    """Remove common store/edition suffixes from EPUB title metadata."""
    clean = normalize_inline_text(title)
    if not clean:
        return None

    clean = clean.replace("\ufffd", "").strip()
    clean = TITLE_PREFIX_PATTERN.sub("", clean).strip()
    suffix_indexes = [
        index
        for starter in TITLE_SUFFIX_STARTERS
        if (index := clean.find(starter)) > 0
    ]
    if suffix_indexes:
        clean = clean[: min(suffix_indexes)].strip()

    return clean or None


def make_book_id(metadata: BookMetadata, file_hash: str) -> str:
    """Create a stable ID for a book.

    Prefer the EPUB identifier when present because it survives file movement and
    renaming. Fall back to the content hash when identifier metadata is missing.
    """
    if metadata.identifier:
        raw = f"epub-identifier:{metadata.identifier}".encode("utf-8")
        return hashlib.sha256(raw).hexdigest()
    return file_hash


def file_sha256(file_path: str | Path, block_size: int = HASH_BLOCK_SIZE) -> str:
    """Hash file contents without loading the whole file into memory."""
    digest = hashlib.sha256()
    with open(file_path, "rb") as file:
        while block := file.read(block_size):
            digest.update(block)
    return digest.hexdigest()


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


def extract_html_documents(book: epub.EpubBook) -> list[dict[str, Any]]:
    """Return HTML documents in EPUB reading order."""
    documents: list[dict[str, Any]] = []
    seen_hrefs: set[str] = set()

    for idref, _linear in book.spine:
        item = book.get_item_with_id(idref)
        if item is None or item.get_type() != ITEM_DOCUMENT:
            continue

        href = item.get_name()
        if href in seen_hrefs:
            continue

        documents.append({"href": href, "html": item.get_content()})
        seen_hrefs.add(href)

    if documents:
        return documents

    for item in book.get_items_of_type(ITEM_DOCUMENT):
        href = item.get_name()
        if href in seen_hrefs:
            continue

        documents.append({"href": href, "html": item.get_content()})
        seen_hrefs.add(href)

    return documents


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


def normalize_inline_text(text: Any) -> str:
    """Normalize one inline metadata or paragraph value."""
    if text is None:
        return ""
    clean = str(text).replace("\ufffd", "")
    return re.sub(r"\s+", " ", clean).strip()


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

