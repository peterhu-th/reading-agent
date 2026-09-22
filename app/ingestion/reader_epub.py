from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any, Iterable
from urllib.parse import unquote, urlsplit

from bs4 import BeautifulSoup, NavigableString, Tag
from ebooklib import epub

from app.ingestion.chunking import infer_book_type
from app.ingestion.load_epub import (
    clean_book_title,
    extract_html_documents,
    extract_metadata,
    file_sha256,
    make_book_id,
    normalize_inline_text,
    read_epub_file,
)
from app.models.schemas import ReaderContentRecord


BLOCK_TAGS = ("h1", "h2", "h3", "h4", "h5", "h6", "p", "li", "blockquote", "pre")


@dataclass(frozen=True)
class TocEntry:
    title: str
    href: str
    depth: int


@dataclass(frozen=True)
class ReaderBlock:
    text: str
    kind: str
    node: Tag
    source_href: str
    node_path: str
    line_index: int


@dataclass(frozen=True)
class ReaderChapterContent:
    title: str
    depth: int
    blocks: list[ReaderBlock]


def load_epub_for_reader(epub_path: str | Path) -> list[ReaderContentRecord]:
    """Extract every readable text block while preserving EPUB navigation."""

    path = Path(epub_path).expanduser().resolve()
    book = read_epub_file(path)
    metadata = extract_metadata(book)
    title = metadata.title or clean_book_title(path.stem) or path.stem
    author = ", ".join(metadata.authors)
    book_id = make_book_id(metadata, file_sha256(path))
    book_type = infer_book_type(title)
    chapters = extract_reader_chapters(book, book_type)
    records: list[ReaderContentRecord] = []
    for chapter_index, chapter in enumerate(chapters):
        for paragraph_index, block in enumerate(chapter.blocks):
            records.append(
                ReaderContentRecord(
                    book_id=book_id,
                    title=title,
                    author=author,
                    book_type=book_type,
                    chapter_index=chapter_index,
                    chapter_title=chapter.title,
                    chapter_depth=chapter.depth,
                    paragraph_index=paragraph_index,
                    text=block.text,
                    kind=block.kind,
                    source_epub=path.name,
                    source_href=block.source_href,
                    source_node_path=block.node_path,
                    source_line_index=block.line_index,
                )
            )
    return records


def extract_reader_chapters(book: epub.EpubBook, book_type: str = "fiction") -> list[ReaderChapterContent]:
    toc_by_document: dict[str, list[TocEntry]] = {}
    for entry in flatten_toc(book.toc):
        document_path = normalized_document_path(entry.href)
        if document_path:
            toc_by_document.setdefault(document_path, []).append(entry)

    chapters: list[ReaderChapterContent] = []
    note_blocks: list[ReaderBlock] = []
    unnamed_index = 1
    for document in extract_html_documents(book):
        soup = BeautifulSoup(document["html"], "lxml")
        for tag in soup(["script", "style", "noscript"]):
            tag.decompose()
        blocks = extract_reader_blocks(
            soup,
            source_href=str(document["href"]),
            poetry=book_type == "poetry",
        )
        if not blocks:
            continue

        entries = toc_by_document.get(normalized_document_path(document["href"]), [])
        if entries:
            chapters.extend(split_document_by_toc(soup, blocks, entries))
            continue

        heading = next((block.text for block in blocks if block.kind == "heading"), "")
        if is_note_document(blocks, heading):
            note_blocks.extend(blocks)
            continue
        title = heading or f"未列入目录的内容 {unnamed_index}"
        unnamed_index += 1
        chapters.append(ReaderChapterContent(title=title, depth=0, blocks=blocks))

    if note_blocks:
        chapters.append(ReaderChapterContent(title="注释与附加内容", depth=0, blocks=note_blocks))
    return chapters


def flatten_toc(nodes: Iterable[Any], depth: int = 0) -> list[TocEntry]:
    entries: list[TocEntry] = []
    seen: set[str] = set()

    def visit(items: Iterable[Any], level: int) -> None:
        for item in items:
            if isinstance(item, tuple):
                section, children = item
                add(section, level)
                visit(children, level + 1)
            else:
                add(item, level)

    def add(item: Any, level: int) -> None:
        href = str(getattr(item, "href", "") or "").strip()
        title = normalize_inline_text(getattr(item, "title", ""))
        if not href or not title or href in seen:
            return
        seen.add(href)
        entries.append(TocEntry(title=title, href=href, depth=level))

    visit(nodes, depth)
    return entries


def normalized_document_path(href: str) -> str:
    path = unquote(urlsplit(str(href)).path).replace("\\", "/")
    while path.startswith("./"):
        path = path[2:]
    return path


def extract_reader_blocks(
    soup: BeautifulSoup,
    source_href: str = "",
    poetry: bool = False,
) -> list[ReaderBlock]:
    blocks: list[ReaderBlock] = []
    for node in soup.find_all(BLOCK_TAGS):
        if node.find_parent(BLOCK_TAGS):
            continue
        lines = block_text_lines(node)
        if not lines:
            continue
        kind = block_kind(node, poetry)
        path = tag_path(node)
        blocks.extend(
            ReaderBlock(
                text=line,
                kind=kind,
                node=node,
                source_href=source_href,
                node_path=path,
                line_index=line_index,
            )
            for line_index, line in enumerate(lines)
        )
    if blocks:
        return blocks

    return extract_fallback_blocks(soup, source_href, poetry)


def block_text_lines(node: Tag) -> list[str]:
    """Read one block without treating inline markup as paragraph breaks."""

    explicit_lines: list[list[str]] = [[]]
    for descendant in node.descendants:
        if isinstance(descendant, NavigableString):
            explicit_lines[-1].append(str(descendant))
        elif isinstance(descendant, Tag) and descendant.name == "br":
            explicit_lines.append([])

    raw_lines = ["".join(parts) for parts in explicit_lines]
    if node.name == "pre":
        raw_lines = [line for text in raw_lines for line in text.splitlines()]
    lines = [normalize_inline_text(line) for line in raw_lines]
    return [line for line in lines if line]


def extract_fallback_blocks(soup: BeautifulSoup, source_href: str, poetry: bool) -> list[ReaderBlock]:
    """Extract text from non-standard containers while retaining editable paths."""

    fallback: list[ReaderBlock] = []
    ignored = {"html", "head", "script", "style", "noscript"}
    for node in soup.find_all(True):
        if node.name in ignored or node.find_parent(["head", "script", "style", "noscript"]):
            continue
        direct_text = "\n".join(str(child) for child in node.children if isinstance(child, NavigableString))
        lines = [normalize_inline_text(line) for line in direct_text.splitlines()]
        lines = [line for line in lines if line]
        if not lines:
            continue
        path = tag_path(node)
        fallback.extend(
            ReaderBlock(
                text=line,
                kind="verse" if poetry else "paragraph",
                node=node,
                source_href=source_href,
                node_path=path,
                line_index=line_index,
            )
            for line_index, line in enumerate(lines)
        )
    return fallback


def tag_path(node: Tag) -> str:
    """Return a stable element-index path inside one parsed XHTML document."""

    parts: list[str] = []
    current: Any = node
    while getattr(current, "parent", None) is not None:
        parent = current.parent
        siblings = [child for child in parent.children if isinstance(child, Tag)]
        if current not in siblings:
            break
        parts.append(str(siblings.index(current)))
        current = parent
        if isinstance(current, BeautifulSoup):
            break
    return "/".join(reversed(parts))


def block_kind(node: Tag, poetry: bool) -> str:
    if node.name and node.name.startswith("h"):
        return "heading"
    if node.name == "blockquote":
        return "quote"
    if node.name == "li":
        return "list"
    if node.name == "pre":
        return "verse"
    classes = " ".join(str(value).lower() for value in node.get("class", []))
    if poetry or any(token in classes for token in ("poem", "verse", "line", "center")):
        return "verse"
    return "paragraph"


def split_document_by_toc(soup: BeautifulSoup, blocks: list[ReaderBlock], entries: list[TocEntry]) -> list[ReaderChapterContent]:
    starts: list[tuple[int, TocEntry]] = []
    for entry in entries:
        fragment = unquote(urlsplit(entry.href).fragment)
        start = block_index_for_anchor(soup, blocks, fragment) if fragment else 0
        if not starts or starts[-1][0] != start:
            starts.append((start, entry))
    starts.sort(key=lambda item: item[0])

    result: list[ReaderChapterContent] = []
    if starts and starts[0][0] > 0:
        prefix = blocks[: starts[0][0]]
        if prefix:
            title = next((block.text for block in prefix if block.kind == "heading"), "目录前内容")
            result.append(ReaderChapterContent(title=title, depth=0, blocks=prefix))
    for index, (start, entry) in enumerate(starts):
        end = starts[index + 1][0] if index + 1 < len(starts) else len(blocks)
        selected = blocks[start:end]
        if selected:
            result.append(ReaderChapterContent(title=entry.title, depth=entry.depth, blocks=selected))
    return result or [ReaderChapterContent(title=entries[0].title, depth=entries[0].depth, blocks=blocks)]


def block_index_for_anchor(soup: BeautifulSoup, blocks: list[ReaderBlock], fragment: str) -> int:
    if not fragment:
        return 0
    anchor = soup.find(id=fragment) or soup.find(attrs={"name": fragment})
    if anchor is None:
        return 0
    block_node = anchor if getattr(anchor, "name", "") in BLOCK_TAGS else anchor.find_parent(BLOCK_TAGS) or anchor.find_next(BLOCK_TAGS)
    if block_node is None:
        return 0
    return next((index for index, block in enumerate(blocks) if block.node is block_node), 0)


def is_note_document(blocks: list[ReaderBlock], heading: str) -> bool:
    if heading:
        return False
    sample = "".join(block.text for block in blocks[:3]).lstrip()
    return sample.startswith(("校注：", "译注：", "注：", "脚注：", "尾注：", "编注："))
