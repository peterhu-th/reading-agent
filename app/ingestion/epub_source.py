from __future__ import annotations

import hashlib
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from bs4 import BeautifulSoup, UnicodeDammit
from ebooklib import ITEM_DOCUMENT, epub

HASH_BLOCK_SIZE = 1024 * 1024
TITLE_SUFFIX_STARTERS = ("（", "(", "【", "[")
TITLE_PREFIX_PATTERN = re.compile(r"^(?:【[^】]{1,20}】|\[[^\]]{1,20}\])\s*")
XML_DECLARATION_PATTERN = re.compile(r"\A(\ufeff?\s*)<\?xml\s+[^?]*\?>", re.IGNORECASE)


@dataclass(frozen=True)
class BookMetadata:
    title: str | None = None
    authors: tuple[str, ...] = ()
    identifier: str | None = None
    language: str | None = None
    publisher: str | None = None
    description: str | None = None


def parse_epub_html(markup: bytes | str) -> BeautifulSoup:
    """Parse EPUB HTML/XHTML with the reader's established HTML tree rules.

    Keep lxml's HTML repair and named-entity handling so existing element paths
    remain editable. Decode before removing the XML declaration to retain its
    encoding; the declaration itself is not part of an element's source path.
    NCX navigation is actual XML and should continue to use an XML parser.
    """
    if isinstance(markup, bytes):
        decoded = UnicodeDammit(markup, is_html=True).unicode_markup
        if decoded is None:
            raise ValueError("Cannot decode EPUB HTML content")
        markup = decoded
    markup = XML_DECLARATION_PATTERN.sub(r"\1", markup, count=1)
    return BeautifulSoup(markup, "lxml")


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

    Combine an EPUB identifier with bibliographic metadata because some EPUB
    generators reuse one identifier across unrelated books. This remains stable
    across file movement, renaming, and body-text edits. Fall back to the content
    hash when identifier metadata is missing.
    """
    if metadata.identifier:
        identity = "\0".join(
            (
                metadata.identifier,
                metadata.title or "",
                *metadata.authors,
            )
        )
        raw = f"epub-identity:{identity}".encode("utf-8")
        return hashlib.sha256(raw).hexdigest()
    return file_hash


def file_sha256(file_path: str | Path, block_size: int = HASH_BLOCK_SIZE) -> str:
    """Hash file contents without loading the whole file into memory."""
    digest = hashlib.sha256()
    with open(file_path, "rb") as file:
        while block := file.read(block_size):
            digest.update(block)
    return digest.hexdigest()


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


def normalize_inline_text(text: Any) -> str:
    """Normalize one inline metadata or paragraph value."""
    if text is None:
        return ""
    clean = str(text).replace("\ufffd", "")
    return re.sub(r"\s+", " ", clean).strip()
