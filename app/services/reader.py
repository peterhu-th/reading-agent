import json
import hashlib
from collections import defaultdict
from functools import cached_property
from pathlib import Path
from threading import RLock
from typing import Any

from app.config import get_settings
from app.models.reader_tools import PaginatedResponse, SearchResultItem
from app.models.schemas import (
    BookParagraph,
    ReaderBook,
    ReaderChapter,
    ReaderContentRecord,
    ReaderLocation,
    ReaderPage,
    ReaderParagraph,
    ReaderSearchHit,
)


class ReaderNotFoundError(KeyError):
    pass


class ReaderService:
    """Serve complete EPUB text from the reader-only, lossless data store."""

    def __init__(
        self,
        reader_path: str | Path | None = None,
        books_path: str | Path | None = None,
        chunks_path: str | Path | None = None,
    ) -> None:
        settings = get_settings()
        self.reader_path = Path(reader_path or settings.READER_JSONL_PATH)
        self.books_path = Path(books_path or settings.BOOKS_JSONL_PATH)
        self.chunks_path = Path(chunks_path or settings.CHUNKS_JSONL_PATH)
        self.editor: Any | None = None
        self._lock = RLock()
        self._chapter_records: dict[str, dict[int, list[ReaderContentRecord]]] = {}
        self._chapter_metadata: dict[str, list[ReaderChapter]] = {}
        self._chapter_positions: dict[str, dict[int, int]] = {}
        self._book_metadata: dict[str, ReaderBook] = {}

    def set_editor(self, editor: Any) -> None:
        with self._lock:
            self.editor = editor
            self.invalidate_effective()

    def reload(self) -> None:
        """Discard the current snapshot so the next access reads files again."""

        with self._lock:
            for name in ("records", "records_by_book", "chapters"):
                self.__dict__.pop(name, None)
            self._chapter_records.clear()
            self.invalidate_effective()

    def replace_records(
        self,
        records: list[ReaderContentRecord],
        affected_book_ids: set[str] | None = None,
    ) -> None:
        """Install saved records while retaining the views of unchanged books."""

        with self._lock:
            if affected_book_ids is None:
                self.reload()
            else:
                grouped = self.__dict__.get("records_by_book")
                if grouped is not None:
                    for book_id in affected_book_ids:
                        grouped.pop(book_id, None)
                    for record in records:
                        if record.book_id in affected_book_ids:
                            grouped.setdefault(record.book_id, []).append(record)
                for book_id in affected_book_ids:
                    self._chapter_records.pop(book_id, None)
                self.__dict__.pop("chapters", None)
                self.invalidate_effective(affected_book_ids)
            self.__dict__["records"] = records

    def invalidate_effective(self, book_ids: set[str] | None = None) -> None:
        with self._lock:
            if book_ids is None:
                self._chapter_metadata.clear()
                self._chapter_positions.clear()
                self._book_metadata.clear()
            else:
                for book_id in book_ids:
                    self._chapter_metadata.pop(book_id, None)
                    self._chapter_positions.pop(book_id, None)
                    self._book_metadata.pop(book_id, None)

    def effective_records(self, records: list[ReaderContentRecord]) -> list[ReaderContentRecord]:
        if self.editor is None:
            return records
        return self.editor.apply_records(records)

    @cached_property
    def records(self) -> list[ReaderContentRecord]:
        if self.reader_path.exists():
            return self._load_reader_records(self.reader_path)
        return self._legacy_records()

    @staticmethod
    def _load_reader_records(path: Path) -> list[ReaderContentRecord]:
        records: list[ReaderContentRecord] = []
        with path.open("r", encoding="utf-8") as file:
            for line in file:
                if line.strip():
                    records.append(ReaderContentRecord(**json.loads(line)))
        return records

    def _legacy_records(self) -> list[ReaderContentRecord]:
        """Keep the reader usable before build_reader_library.py is run."""

        if not self.books_path.exists():
            return []
        book_types = self._legacy_book_types()
        records: list[ReaderContentRecord] = []
        with self.books_path.open("r", encoding="utf-8") as file:
            for line in file:
                if not line.strip():
                    continue
                paragraph = BookParagraph(**json.loads(line))
                records.append(
                    ReaderContentRecord(
                        book_id=paragraph.book_id,
                        title=paragraph.title,
                        author=paragraph.author,
                        book_type=book_types.get(paragraph.book_id, "fiction"),
                        chapter_index=paragraph.chapter_index,
                        chapter_title=paragraph.chapter_title,
                        paragraph_index=paragraph.paragraph_index,
                        text=paragraph.text,
                    )
                )
        return records

    def _legacy_book_types(self) -> dict[str, str]:
        result: dict[str, str] = {}
        if not self.chunks_path.exists():
            return result
        with self.chunks_path.open("r", encoding="utf-8") as file:
            for line in file:
                if line.strip():
                    data = json.loads(line)
                    result.setdefault(str(data.get("book_id", "")), str(data.get("book_type", "fiction")))
        return result

    @cached_property
    def records_by_book(self) -> dict[str, list[ReaderContentRecord]]:
        grouped: dict[str, list[ReaderContentRecord]] = defaultdict(list)
        for record in self.records:
            grouped[record.book_id].append(record)
        return dict(grouped)

    def _chapters_for_book(self, book_id: str) -> dict[int, list[ReaderContentRecord]]:
        if book_id in self._chapter_records:
            return self._chapter_records[book_id]
        records = self.records_by_book.get(book_id)
        if records is None:
            raise ReaderNotFoundError(book_id)
        grouped: dict[int, list[ReaderContentRecord]] = defaultdict(list)
        for record in records:
            grouped[record.chapter_index].append(record)
        for values in grouped.values():
            values.sort(key=lambda item: item.paragraph_index)
        self._chapter_records[book_id] = dict(sorted(grouped.items()))
        return self._chapter_records[book_id]

    @cached_property
    def chapters(self) -> dict[tuple[str, int], list[ReaderContentRecord]]:
        return {
            (book_id, chapter_index): records
            for book_id in self.records_by_book
            for chapter_index, records in self._chapters_for_book(book_id).items()
        }

    @property
    def books(self) -> list[ReaderBook]:
        with self._lock:
            return sorted(self.book_by_id.values(), key=lambda item: item.title)

    @property
    def book_by_id(self) -> dict[str, ReaderBook]:
        with self._lock:
            return {book_id: self.get_book(book_id) for book_id in self.records_by_book}

    def get_book(self, book_id: str) -> ReaderBook:
        with self._lock:
            if book_id in self._book_metadata:
                return self._book_metadata[book_id]
            records = self.records_by_book.get(book_id)
            if not records:
                raise ReaderNotFoundError(book_id)
            first = records[0]
            chapters = self.list_chapters(book_id)
            book = ReaderBook(
                book_id=book_id,
                title=first.title,
                author=first.author,
                book_type=first.book_type,
                chapter_count=len(chapters),
                paragraph_count=sum(chapter.paragraph_count for chapter in chapters),
            )
            self._book_metadata[book_id] = book
            return book

    def list_chapters(self, book_id: str) -> list[ReaderChapter]:
        with self._lock:
            if book_id in self._chapter_metadata:
                return self._chapter_metadata[book_id]
            result: list[ReaderChapter] = []
            for chapter_index, records in self._chapters_for_book(book_id).items():
                effective = self.effective_records(records)
                if not effective:
                    continue
                first = effective[0]
                result.append(
                    ReaderChapter(
                        book_id=book_id,
                        chapter_index=chapter_index,
                        chapter_title=first.chapter_title.strip() or f"第 {chapter_index + 1} 章",
                        depth=first.chapter_depth,
                        paragraph_count=len(effective),
                        preview=make_excerpt(next((item.text for item in effective if item.kind != "heading"), first.text), 80),
                    )
                )
            self._chapter_metadata[book_id] = result
            self._chapter_positions[book_id] = {chapter.chapter_index: position for position, chapter in enumerate(result)}
            return result

    def get_chapter(
        self,
        book_id: str,
        chapter_index: int,
        offset: int = 0,
        limit: int = 80,
        focus_paragraph: int | None = None,
    ) -> ReaderPage:
        with self._lock:
            book = self.get_book(book_id)
            base_records = self._chapters_for_book(book_id).get(chapter_index)
            if base_records is None:
                raise ReaderNotFoundError(f"{book_id}:{chapter_index}")
            records = self.effective_records(base_records)
            if not records:
                raise ReaderNotFoundError(f"{book_id}:{chapter_index}")
            if focus_paragraph is not None and records:
                focus_position = next((index for index, item in enumerate(records) if item.paragraph_index >= focus_paragraph), len(records) - 1)
                leading_context = min(12, max(1, limit // 4))
                offset = max(0, focus_position - leading_context)
            chapter_list = self.list_chapters(book_id)
            position = self._chapter_positions[book_id][chapter_index]
            selected = records[offset : offset + limit]
            return ReaderPage(
                book=book,
                chapter=chapter_list[position],
                paragraphs=[
                    ReaderParagraph(
                        paragraph_index=item.paragraph_index,
                        edit_id=reader_edit_id(item),
                        text=item.text,
                        kind=item.kind,
                        annotations=self.editor.annotations_for_record(item) if self.editor else [],
                    )
                    for item in selected
                ],
                offset=offset,
                next_offset=offset + limit if offset + limit < len(records) else None,
                previous_chapter_index=chapter_list[position - 1].chapter_index if position > 0 else None,
                next_chapter_index=chapter_list[position + 1].chapter_index if position + 1 < len(chapter_list) else None,
            )

    def search(self, book_id: str, query: str, chapter_index: int | None = None, limit: int = 30) -> list[ReaderSearchHit]:
        """Keep the list response used by existing service callers."""

        if not query.strip():
            return []
        page = self.search_page(book_id, query, chapter_index=chapter_index, limit=limit)
        return [ReaderSearchHit(**item.model_dump()) for item in page.items]

    def search_page(
        self,
        book_id: str,
        query: str,
        chapter_index: int | None = None,
        offset: int = 0,
        limit: int = 30,
    ) -> PaginatedResponse[SearchResultItem]:
        with self._lock:
            self.get_book(book_id)
            needle = query.strip().lower()
            if not needle:
                raise ValueError("搜索关键词不能为空。")
            if chapter_index is not None and chapter_index not in self._chapter_positions[book_id]:
                raise ReaderNotFoundError(f"{book_id}:{chapter_index}")
            hits: list[SearchResultItem] = []
            total = 0
            for candidate_chapter, base_records in self._chapters_for_book(book_id).items():
                if chapter_index is not None and candidate_chapter != chapter_index:
                    continue
                records = self.effective_records(base_records)
                if not records:
                    continue
                title = records[0].chapter_title.strip() or f"第 {candidate_chapter + 1} 章"
                for record in records:
                    if needle in record.text.lower():
                        if offset <= total < offset + limit:
                            hits.append(
                                SearchResultItem(
                                    book_id=book_id,
                                    edit_id=reader_edit_id(record),
                                    chapter_index=candidate_chapter,
                                    chapter_title=title,
                                    paragraph_index=record.paragraph_index,
                                    excerpt=highlight_excerpt(record.text, needle),
                                    reader_location=ReaderLocation(
                                        book_id=book_id,
                                        chapter_index=candidate_chapter,
                                        start_paragraph_index=record.paragraph_index,
                                        end_paragraph_index=record.paragraph_index,
                                    ),
                                )
                            )
                        total += 1
            return PaginatedResponse[SearchResultItem](
                items=hits,
                total=total,
                offset=offset,
                limit=limit,
                next_offset=offset + limit if offset + limit < total else None,
            )

    def locate_edit_id(self, book_id: str, edit_id: str) -> ReaderLocation | None:
        """Resolve a paragraph identity in the current document, including edits."""

        with self._lock:
            if self.editor is not None:
                return self.editor.resolve_location(book_id, edit_id)
            for record in self.records_by_book.get(book_id, []):
                if reader_edit_id(record) == edit_id:
                    return ReaderLocation(
                        book_id=book_id,
                        chapter_index=record.chapter_index,
                        start_paragraph_index=record.paragraph_index,
                        end_paragraph_index=record.paragraph_index,
                    )
            return None

    def locate(self, book_id: str, source_text: str) -> ReaderLocation | None:
        """Map a filtered RAG chunk back to its lossless reader location."""

        with self._lock:
            compact_source = compact_text(source_text)
            if not compact_source:
                return None
            probe = compact_source[:80]
            if book_id not in self.records_by_book:
                return None
            for chapter_index, base_records in self._chapters_for_book(book_id).items():
                records = self.effective_records(base_records)
                for position, record in enumerate(records):
                    if probe in compact_text(record.text):
                        return ReaderLocation(book_id=book_id, chapter_index=chapter_index, start_paragraph_index=record.paragraph_index, end_paragraph_index=record.paragraph_index)
                    joined = compact_text("".join(item.text for item in records[position : position + 5]))
                    if probe in joined:
                        return ReaderLocation(
                            book_id=book_id,
                            chapter_index=chapter_index,
                            start_paragraph_index=record.paragraph_index,
                            end_paragraph_index=records[min(position + 4, len(records) - 1)].paragraph_index,
                        )
            return None


def compact_text(text: str) -> str:
    return "".join(text.split())


def reader_edit_id(record: ReaderContentRecord) -> str:
    if record.source_epub and record.source_href and record.source_node_path:
        parts = [
            record.book_id,
            record.source_epub,
            record.source_href,
            record.source_node_path,
            str(record.source_line_index),
        ]
    else:
        parts = [record.book_id, str(record.chapter_index), str(record.paragraph_index)]
    locator = "|".join(parts)
    return hashlib.sha256(locator.encode("utf-8")).hexdigest()[:24]


def make_excerpt(text: str, limit: int) -> str:
    compact = " ".join(text.split())
    return compact if len(compact) <= limit else compact[:limit].rstrip() + "…"


def highlight_excerpt(text: str, needle: str, radius: int = 55) -> str:
    lowered = text.lower()
    index = lowered.find(needle)
    if index < 0:
        return make_excerpt(text, radius * 2)
    start = max(0, index - radius)
    end = min(len(text), index + len(needle) + radius)
    return ("…" if start else "") + text[start:end] + ("…" if end < len(text) else "")
