import json
import hashlib
from collections import defaultdict
from functools import cached_property
from pathlib import Path
from typing import Any

from app.config import get_settings
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

    def set_editor(self, editor: Any) -> None:
        self.editor = editor
        self.invalidate_effective()

    def reload(self) -> None:
        """Discard the current snapshot so the next access reads files again."""

        for name in ("records", "chapters", "books", "book_by_id"):
            self.__dict__.pop(name, None)

    def invalidate_effective(self) -> None:
        for name in ("books", "book_by_id"):
            self.__dict__.pop(name, None)

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
    def chapters(self) -> dict[tuple[str, int], list[ReaderContentRecord]]:
        grouped: dict[tuple[str, int], list[ReaderContentRecord]] = defaultdict(list)
        for record in self.records:
            grouped[(record.book_id, record.chapter_index)].append(record)
        for values in grouped.values():
            values.sort(key=lambda item: item.paragraph_index)
        return dict(grouped)

    @cached_property
    def books(self) -> list[ReaderBook]:
        grouped: dict[str, dict] = {}
        for record in self.records:
            grouped.setdefault(
                record.book_id,
                {"title": record.title, "author": record.author, "book_type": record.book_type, "chapters": set(), "paragraph_count": 0},
            )
        for record in self.effective_records(self.records):
            item = grouped[record.book_id]
            item["chapters"].add(record.chapter_index)
            item["paragraph_count"] += 1
        return sorted(
            [
                ReaderBook(
                    book_id=book_id,
                    title=item["title"],
                    author=item["author"],
                    book_type=item["book_type"],
                    chapter_count=len(item["chapters"]),
                    paragraph_count=item["paragraph_count"],
                )
                for book_id, item in grouped.items()
            ],
            key=lambda item: item.title,
        )

    @cached_property
    def book_by_id(self) -> dict[str, ReaderBook]:
        return {book.book_id: book for book in self.books}

    def get_book(self, book_id: str) -> ReaderBook:
        book = self.book_by_id.get(book_id)
        if book is None:
            raise ReaderNotFoundError(book_id)
        return book

    def list_chapters(self, book_id: str) -> list[ReaderChapter]:
        self.get_book(book_id)
        result: list[ReaderChapter] = []
        for (candidate_id, chapter_index), records in self.chapters.items():
            if candidate_id != book_id or not records:
                continue
            effective = self.effective_records(records)
            if not effective:
                continue
            first = effective[0] if effective else records[0]
            result.append(
                ReaderChapter(
                    book_id=book_id,
                    chapter_index=chapter_index,
                    chapter_title=first.chapter_title.strip() or f"第 {chapter_index + 1} 章",
                    depth=first.chapter_depth,
                    paragraph_count=len(effective),
                    preview=make_excerpt(next((item.text for item in effective if item.kind != "heading"), first.text if effective else ""), 80),
                )
            )
        return sorted(result, key=lambda item: item.chapter_index)

    def get_chapter(
        self,
        book_id: str,
        chapter_index: int,
        offset: int = 0,
        limit: int = 80,
        focus_paragraph: int | None = None,
    ) -> ReaderPage:
        book = self.get_book(book_id)
        base_records = self.chapters.get((book_id, chapter_index))
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
        positions = [item.chapter_index for item in chapter_list]
        position = positions.index(chapter_index)
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
            previous_chapter_index=positions[position - 1] if position > 0 else None,
            next_chapter_index=positions[position + 1] if position + 1 < len(positions) else None,
        )

    def search(self, book_id: str, query: str, chapter_index: int | None = None, limit: int = 30) -> list[ReaderSearchHit]:
        self.get_book(book_id)
        needle = query.strip().lower()
        if not needle:
            return []
        hits: list[ReaderSearchHit] = []
        for (candidate_id, candidate_chapter), base_records in self.chapters.items():
            if candidate_id != book_id or (chapter_index is not None and candidate_chapter != chapter_index):
                continue
            records = self.effective_records(base_records)
            if not records:
                continue
            title = records[0].chapter_title.strip() or f"第 {candidate_chapter + 1} 章"
            for record in records:
                if needle in record.text.lower():
                    hits.append(
                        ReaderSearchHit(
                            chapter_index=candidate_chapter,
                            chapter_title=title,
                            paragraph_index=record.paragraph_index,
                            excerpt=highlight_excerpt(record.text, needle),
                        )
                    )
                    if len(hits) >= limit:
                        return hits
        return hits

    def locate(self, book_id: str, source_text: str) -> ReaderLocation | None:
        """Map a filtered RAG chunk back to its lossless reader location."""

        compact_source = compact_text(source_text)
        if not compact_source:
            return None
        probe = compact_source[:80]
        for (candidate_id, chapter_index), base_records in self.chapters.items():
            if candidate_id != book_id:
                continue
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
