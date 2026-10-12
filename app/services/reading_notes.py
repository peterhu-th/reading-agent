"""Read book notes and exports from one consistent, current editor snapshot."""

from collections import defaultdict

from app.models.reader_tools import (
    AnnotationListItem,
    BookmarkListItem,
    NotesExport,
    PaginatedResponse,
    ReaderBookmark,
)
from app.models.schemas import ReaderAnnotation, ReaderContentRecord, ReaderLocation
from app.services.editor_session import EditorSessionService
from app.services.reader import ReaderNotFoundError, ReaderService, make_excerpt, reader_edit_id


class ReadingNotesService:
    def __init__(self, reader: ReaderService, editor: EditorSessionService) -> None:
        self.reader = reader
        self.editor = editor

    def list_annotations(
        self, book_id: str, offset: int = 0, limit: int = 50, chapter_index: int | None = None,
    ) -> PaginatedResponse[AnnotationListItem]:
        with self.reader._lock:
            self._check_book(book_id, chapter_index)
            snapshot = self.editor.reading_snapshot(book_id)
            source = _NoteSources(snapshot.records)
            annotations = sorted(
                (item for item in snapshot.annotations if chapter_index is None or item.chapter_index == chapter_index),
                key=lambda item: (*source.sort_position(item.chapter_index, item.start_edit_id), item.start_offset, item.created_at, item.annotation_id),
            )
            return PaginatedResponse[AnnotationListItem](
                items=[source.annotation(item) for item in annotations[offset:offset + limit]],
                total=len(annotations), offset=offset, limit=limit,
                next_offset=offset + limit if offset + limit < len(annotations) else None,
            )

    def list_bookmarks(
        self, book_id: str, offset: int = 0, limit: int = 50, chapter_index: int | None = None,
    ) -> PaginatedResponse[BookmarkListItem]:
        with self.reader._lock:
            self._check_book(book_id, chapter_index)
            snapshot = self.editor.reading_snapshot(book_id)
            source = _NoteSources(snapshot.records)
            bookmarks = sorted(
                (item for item in snapshot.bookmarks if chapter_index is None or item.chapter_index == chapter_index),
                key=lambda item: (*source.sort_position(item.chapter_index, item.edit_id), item.created_at, item.bookmark_id),
            )
            return PaginatedResponse[BookmarkListItem](
                items=[source.bookmark(item) for item in bookmarks[offset:offset + limit]],
                total=len(bookmarks), offset=offset, limit=limit,
                next_offset=offset + limit if offset + limit < len(bookmarks) else None,
            )

    def annotation_location(self, book_id: str, annotation_id: str) -> ReaderLocation | None:
        with self.reader._lock:
            self._check_book(book_id)
            snapshot = self.editor.reading_snapshot(book_id)
            annotation = next((item for item in snapshot.annotations if item.annotation_id == annotation_id), None)
            if annotation is None:
                raise KeyError(annotation_id)
            return _NoteSources(snapshot.records).annotation(annotation).reader_location

    def bookmark_location(self, book_id: str, bookmark_id: str) -> ReaderLocation | None:
        with self.reader._lock:
            self._check_book(book_id)
            snapshot = self.editor.reading_snapshot(book_id)
            bookmark = next((item for item in snapshot.bookmarks if item.bookmark_id == bookmark_id), None)
            if bookmark is None:
                raise KeyError(bookmark_id)
            return _NoteSources(snapshot.records).bookmark(bookmark).reader_location

    def export_snapshot(self, book_id: str) -> NotesExport:
        with self.reader._lock:
            book = self.reader.get_book(book_id)
            snapshot = self.editor.reading_snapshot(book_id)
            source = _NoteSources(snapshot.records)
            annotations = sorted(snapshot.annotations, key=lambda item: (
                *source.sort_position(item.chapter_index, item.start_edit_id), item.start_offset, item.created_at, item.annotation_id,
            ))
            bookmarks = sorted(snapshot.bookmarks, key=lambda item: (
                *source.sort_position(item.chapter_index, item.edit_id), item.created_at, item.bookmark_id,
            ))
            return NotesExport(
                book=book,
                has_unsaved_changes=snapshot.has_unsaved,
                annotations=[source.annotation(item) for item in annotations],
                bookmarks=[source.bookmark(item) for item in bookmarks],
            )

    def export_markdown(self, book_id: str) -> str:
        return self.render_markdown(self.export_snapshot(book_id))

    @staticmethod
    def render_markdown(snapshot: NotesExport) -> str:
        lines = [
            f"# {_markdown(snapshot.book.title)} — 阅读笔记",
            "",
            f"作者：{_markdown(snapshot.book.author) or '未提供'}",
            f"导出时间：{snapshot.exported_at}",
            f"编辑会话存在未保存修改：{'是' if snapshot.has_unsaved_changes else '否'}",
            "",
            f"## 批注（{len(snapshot.annotations)}）",
        ]
        for item in snapshot.annotations:
            lines.extend([
                "", f"### {_markdown(item.chapter_title) or '未知章节'}", "",
                f"颜色：{item.color}",
                f"创建时间：{item.created_at}",
                f"更新时间：{item.updated_at}",
                f"位置：{_format_location(item.reader_location)}", "",
                *[f"> {_markdown(line)}" for line in item.quote.splitlines()], "",
                f"评注：{_markdown(item.comment) or '无'}",
            ])
        lines.extend(["", f"## 书签（{len(snapshot.bookmarks)}）"])
        for item in snapshot.bookmarks:
            lines.extend([
                "", f"### {_markdown(item.chapter_title) or '未知章节'}", "",
                f"名称：{_markdown(item.label) or '未命名书签'}",
                f"创建时间：{item.created_at}",
                f"位置：{_format_location(item.reader_location)}", "",
                *[f"> {_markdown(line)}" for line in item.quote.splitlines()],
            ])
        return "\n".join(lines) + "\n"

    def _check_book(self, book_id: str, chapter_index: int | None = None) -> None:
        self.reader.get_book(book_id)
        if chapter_index is not None and not any(
            chapter.chapter_index == chapter_index for chapter in self.reader.list_chapters(book_id)
        ):
            raise ReaderNotFoundError(f"{book_id}:{chapter_index}")


class _NoteSources:
    def __init__(self, records: list[ReaderContentRecord]) -> None:
        self.by_id = {reader_edit_id(record): record for record in records}
        self.chapters: dict[int, list[ReaderContentRecord]] = defaultdict(list)
        self.positions: dict[str, int] = {}
        for record in sorted(records, key=lambda item: (item.chapter_index, item.paragraph_index)):
            self.positions[reader_edit_id(record)] = len(self.chapters[record.chapter_index])
            self.chapters[record.chapter_index].append(record)

    def sort_position(self, chapter_index: int, edit_id: str) -> tuple[int, int]:
        record = self.by_id.get(edit_id)
        return (record.chapter_index, record.paragraph_index) if record else (chapter_index, 2**63)

    def chapter_title(self, chapter_index: int) -> str:
        records = self.chapters.get(chapter_index, [])
        if not records:
            return ""
        return records[0].chapter_title.strip() or f"第 {chapter_index + 1} 章"

    def annotation(self, annotation: ReaderAnnotation) -> AnnotationListItem:
        start = self.by_id.get(annotation.start_edit_id)
        end = self.by_id.get(annotation.end_edit_id)
        location = None
        quote = annotation.selected_text
        if (
            start is not None and end is not None
            and start.book_id == end.book_id == annotation.book_id
            and start.chapter_index == end.chapter_index == annotation.chapter_index
            and self.positions[annotation.start_edit_id] <= self.positions[annotation.end_edit_id]
            and annotation.start_offset <= len(start.text)
            and annotation.end_offset <= len(end.text)
            and (start is not end or annotation.start_offset <= annotation.end_offset)
        ):
            records = self.chapters[start.chapter_index][
                self.positions[annotation.start_edit_id]:self.positions[annotation.end_edit_id] + 1
            ]
            if len(records) == 1:
                quote = start.text[annotation.start_offset:annotation.end_offset]
            else:
                quote = "\n".join([
                    start.text[annotation.start_offset:],
                    *[record.text for record in records[1:-1]],
                    end.text[:annotation.end_offset],
                ])
            location = ReaderLocation(
                book_id=annotation.book_id, chapter_index=start.chapter_index,
                start_paragraph_index=start.paragraph_index, end_paragraph_index=end.paragraph_index,
            )
        return AnnotationListItem(
            **annotation.model_dump(), chapter_title=self.chapter_title(annotation.chapter_index),
            excerpt=make_excerpt(quote, 180), quote=quote, reader_location=location,
        )

    def bookmark(self, bookmark: ReaderBookmark) -> BookmarkListItem:
        record = self.by_id.get(bookmark.edit_id)
        location = None
        quote = ""
        if record is not None and record.book_id == bookmark.book_id and record.chapter_index == bookmark.chapter_index:
            quote = record.text
            location = ReaderLocation(
                book_id=bookmark.book_id, chapter_index=record.chapter_index,
                start_paragraph_index=record.paragraph_index, end_paragraph_index=record.paragraph_index,
            )
        return BookmarkListItem(
            **bookmark.model_dump(), chapter_title=self.chapter_title(bookmark.chapter_index),
            excerpt=make_excerpt(quote, 180), quote=quote, reader_location=location,
        )


def _markdown(text: str) -> str:
    escaped = text.replace("\\", "\\\\")
    for character in "`*_[]<>#":
        escaped = escaped.replace(character, "\\" + character)
    return escaped


def _format_location(location: ReaderLocation | None) -> str:
    if location is None:
        return "原文位置已失效"
    return (
        f"章节索引 {location.chapter_index}，"
        f"段落索引 {location.start_paragraph_index}–{location.end_paragraph_index}"
    )
