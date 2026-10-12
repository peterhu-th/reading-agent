from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from pathlib import Path
from tempfile import NamedTemporaryFile

from app.config import get_settings
from app.models.reader_tools import AnnotationUpdateInput, BookmarkCreateInput, ReaderBookmark
from app.models.schemas import (
    AnnotationCreateRequest,
    EditorState,
    ReaderAnnotation,
    ReaderContentRecord,
    ReaderLocation,
    ReaderOperationRequest,
    utc_now_iso,
)
from app.services.epub_editor import EpubEditorService, replace_saved_files
from app.services.reader import ReaderService, reader_edit_id


@dataclass
class UndoEntry:
    label: str
    text_before: dict[str, str | None]
    deleted_before: dict[str, bool]
    annotations_before: dict[str, ReaderAnnotation | None] | None = None
    chapter_titles_before: dict[tuple[str, int], str | None] | None = None
    bookmarks_before: dict[str, ReaderBookmark | None] | None = None


@dataclass
class EditorReadingSnapshot:
    records: list[ReaderContentRecord]
    annotations: list[ReaderAnnotation]
    bookmarks: list[ReaderBookmark]
    has_unsaved: bool


class EditorSessionService:
    """Service-lifetime working document plus complete undo history."""

    def __init__(self, reader: ReaderService) -> None:
        settings = get_settings()
        self.reader = reader
        self.annotation_path = Path(settings.ANNOTATIONS_JSON_PATH)
        self.bookmark_path = Path(settings.BOOKMARKS_JSON_PATH)
        self.epub_editor = EpubEditorService(settings.RAW_EPUB_DIR)
        self._lock = reader._lock
        self._base_by_id: dict[str, ReaderContentRecord] = {}
        self._chapter_positions: dict[tuple[str, int], dict[str, int]] = {}
        self._texts: dict[str, str] = {}
        self._deleted: set[str] = set()
        self._annotations: dict[str, ReaderAnnotation] = {}
        self._annotations_by_chapter: dict[tuple[str, int], dict[str, ReaderAnnotation]] = {}
        self._annotations_by_position: dict[tuple[str, int], dict[int, dict[str, ReaderAnnotation]]] = {}
        self._bookmarks: dict[str, ReaderBookmark] = {}
        self._bookmarks_by_edit_id: dict[str, dict[str, ReaderBookmark]] = {}
        self._chapter_titles: dict[tuple[str, int], str] = {}
        self._chapter_base_titles: dict[tuple[str, int], str] = {}
        self._undo: list[UndoEntry] = []
        self._saved_signature = ""

    def reset(self) -> None:
        with self._lock:
            self._texts.clear()
            self._deleted.clear()
            self._chapter_titles.clear()
            self._undo.clear()
            self._annotations = {item.annotation_id: item for item in self._load_annotations()}
            self._bookmarks = {item.bookmark_id: item for item in self._load_bookmarks()}
            self.rebase()
            self._saved_signature = self._signature()

    def rebase(self, affected_book_ids: set[str] | None = None) -> None:
        with self._lock:
            base_by_id: dict[str, ReaderContentRecord] = {}
            chapter_positions: dict[tuple[str, int], dict[str, int]] = {}
            chapter_base_titles: dict[tuple[str, int], str] = {}
            for record in self.reader.records:
                edit_id = reader_edit_id(record)
                base_by_id[edit_id] = record
                positions = chapter_positions.setdefault((record.book_id, record.chapter_index), {})
                positions[edit_id] = len(positions)
                chapter_base_titles.setdefault((record.book_id, record.chapter_index), record.chapter_title)
            self._base_by_id = base_by_id
            self._chapter_positions = chapter_positions
            self._chapter_base_titles = chapter_base_titles
            self._rebuild_annotation_index(affected_book_ids)
            self._rebuild_bookmark_index()
            if self.reader.editor is not self:
                self.reader.set_editor(self)
            else:
                self.reader.invalidate_effective(affected_book_ids)

    def apply_records(self, records: list[ReaderContentRecord]) -> list[ReaderContentRecord]:
        with self._lock:
            result: list[ReaderContentRecord] = []
            for record in records:
                edit_id = reader_edit_id(record)
                if edit_id in self._deleted:
                    continue
                text = self._texts.get(edit_id)
                chapter_title = self._chapter_titles.get((record.book_id, record.chapter_index))
                updates: dict[str, str] = {}
                if text is not None:
                    updates["text"] = text
                if chapter_title is not None:
                    updates["chapter_title"] = chapter_title
                result.append(record.model_copy(update=updates) if updates else record)
            return result

    def annotations_for_record(self, record: ReaderContentRecord) -> list[ReaderAnnotation]:
        with self._lock:
            key = (record.book_id, record.chapter_index)
            position = self._chapter_positions.get(key, {}).get(reader_edit_id(record), -1)
            matches = self._annotations_by_position.get(key, {}).get(position, {}).values()
            return sorted(matches, key=lambda item: (item.created_at, item.annotation_id))

    def annotations_snapshot(self, book_id: str | None = None) -> list[ReaderAnnotation]:
        with self._lock:
            self._ensure_base()
            return [item.model_copy(deep=True) for item in self._annotations.values() if book_id is None or item.book_id == book_id]

    def bookmarks_snapshot(self, book_id: str | None = None) -> list[ReaderBookmark]:
        with self._lock:
            self._ensure_base()
            return [item.model_copy(deep=True) for item in self._bookmarks.values() if book_id is None or item.book_id == book_id]

    def reading_snapshot(self, book_id: str | None = None) -> EditorReadingSnapshot:
        """Copy notes and their effective source text in the same editing state."""
        with self._lock:
            self._ensure_base()
            records = self.reader.records if book_id is None else self.reader.records_by_book.get(book_id, [])
            return EditorReadingSnapshot(
                records=[item.model_copy(deep=True) for item in self.apply_records(records)],
                annotations=self.annotations_snapshot(book_id),
                bookmarks=self.bookmarks_snapshot(book_id),
                has_unsaved=self.has_unsaved_changes,
            )

    def resolve_location(self, book_id: str, start_edit_id: str, end_edit_id: str | None = None) -> ReaderLocation | None:
        with self._lock:
            self._ensure_base()
            end_edit_id = end_edit_id or start_edit_id
            start = self._base_by_id.get(start_edit_id)
            end = self._base_by_id.get(end_edit_id)
            if start is None or end is None or start_edit_id in self._deleted or end_edit_id in self._deleted:
                return None
            if start.book_id != book_id or end.book_id != book_id or start.chapter_index != end.chapter_index:
                return None
            positions = self._chapter_positions[(book_id, start.chapter_index)]
            if positions[start_edit_id] > positions[end_edit_id]:
                return None
            return ReaderLocation(
                book_id=book_id, chapter_index=start.chapter_index,
                start_paragraph_index=start.paragraph_index, end_paragraph_index=end.paragraph_index,
            )

    def _rebuild_bookmark_index(self) -> None:
        self._bookmarks_by_edit_id.clear()
        for bookmark in self._bookmarks.values():
            self._bookmarks_by_edit_id.setdefault(bookmark.edit_id, {})[bookmark.bookmark_id] = bookmark

    def _set_bookmark(self, bookmark: ReaderBookmark) -> None:
        if bookmark.bookmark_id in self._bookmarks:
            self._remove_bookmark(bookmark.bookmark_id)
        self._bookmarks[bookmark.bookmark_id] = bookmark
        self._bookmarks_by_edit_id.setdefault(bookmark.edit_id, {})[bookmark.bookmark_id] = bookmark

    def _remove_bookmark(self, bookmark_id: str) -> ReaderBookmark:
        bookmark = self._bookmarks.pop(bookmark_id)
        matches = self._bookmarks_by_edit_id[bookmark.edit_id]
        matches.pop(bookmark_id)
        if not matches:
            self._bookmarks_by_edit_id.pop(bookmark.edit_id)
        return bookmark

    def _rebuild_annotation_index(self, book_ids: set[str] | None = None) -> None:
        if book_ids is None:
            self._annotations_by_chapter.clear()
            self._annotations_by_position.clear()
        else:
            for key in list(self._annotations_by_chapter):
                if key[0] in book_ids:
                    self._annotations_by_chapter.pop(key)
                    self._annotations_by_position.pop(key, None)
        for annotation in self._annotations.values():
            if book_ids is None or annotation.book_id in book_ids:
                self._index_annotation(annotation)

    def _index_annotation(self, annotation: ReaderAnnotation) -> None:
        key = (annotation.book_id, annotation.chapter_index)
        self._annotations_by_chapter.setdefault(key, {})[annotation.annotation_id] = annotation
        positions = self._chapter_positions.get(key, {})
        start = positions.get(annotation.start_edit_id, -1)
        end = positions.get(annotation.end_edit_id, -1)
        if start < 0 or end < start:
            return
        by_position = self._annotations_by_position.setdefault(key, {})
        for position in range(start, end + 1):
            by_position.setdefault(position, {})[annotation.annotation_id] = annotation

    def _unindex_annotation(self, annotation: ReaderAnnotation) -> None:
        key = (annotation.book_id, annotation.chapter_index)
        chapter = self._annotations_by_chapter[key]
        chapter.pop(annotation.annotation_id)
        if not chapter:
            self._annotations_by_chapter.pop(key)
        positions = self._chapter_positions.get(key, {})
        start = positions.get(annotation.start_edit_id, -1)
        end = positions.get(annotation.end_edit_id, -1)
        by_position = self._annotations_by_position.get(key, {})
        if start >= 0 and end >= start:
            for position in range(start, end + 1):
                annotations = by_position[position]
                annotations.pop(annotation.annotation_id)
                if not annotations:
                    by_position.pop(position)
        if not by_position:
            self._annotations_by_position.pop(key, None)

    def _set_annotation(self, annotation: ReaderAnnotation) -> None:
        previous = self._annotations.get(annotation.annotation_id)
        if previous is not None:
            self._unindex_annotation(previous)
        self._annotations[annotation.annotation_id] = annotation
        self._index_annotation(annotation)

    def _remove_annotation(self, annotation_id: str) -> ReaderAnnotation:
        annotation = self._annotations.pop(annotation_id)
        self._unindex_annotation(annotation)
        return annotation

    def apply_operation(self, request: ReaderOperationRequest) -> EditorState:
        with self._lock:
            self._ensure_base()
            for mutation in request.mutations:
                base = self._base_by_id.get(mutation.edit_id)
                if base is None or base.book_id != request.book_id:
                    raise KeyError(mutation.edit_id)
            text_before: dict[str, str | None] = {}
            deleted_before: dict[str, bool] = {}
            annotations_before: dict[str, ReaderAnnotation | None] = {}
            bookmarks_before: dict[str, ReaderBookmark | None] = {}
            for mutation in request.mutations:
                base = self._base_by_id[mutation.edit_id]
                text_before.setdefault(mutation.edit_id, self._texts.get(mutation.edit_id))
                deleted_before.setdefault(mutation.edit_id, mutation.edit_id in self._deleted)
                current_text = self._texts.get(mutation.edit_id, base.text)
                deleted = mutation.deleted or not mutation.text.strip()
                next_text = "" if deleted else mutation.text
                for annotation_id, previous in self._adjust_annotations(base, current_text, next_text, deleted).items():
                    annotations_before.setdefault(annotation_id, previous)
                if deleted:
                    for bookmark_id in list(self._bookmarks_by_edit_id.get(mutation.edit_id, {})):
                        bookmarks_before.setdefault(bookmark_id, self._remove_bookmark(bookmark_id))
                    self._deleted.add(mutation.edit_id)
                    self._texts.pop(mutation.edit_id, None)
                else:
                    self._deleted.discard(mutation.edit_id)
                    if mutation.text == base.text:
                        self._texts.pop(mutation.edit_id, None)
                    else:
                        self._texts[mutation.edit_id] = mutation.text
            self._undo.append(
                UndoEntry(
                    label=request.label or "编辑正文",
                    text_before=text_before,
                    deleted_before=deleted_before,
                    annotations_before=annotations_before,
                    bookmarks_before=bookmarks_before,
                )
            )
            self.reader.invalidate_effective({request.book_id})
            return self.state()

    def add_annotation(self, request: AnnotationCreateRequest) -> tuple[ReaderAnnotation, EditorState]:
        with self._lock:
            self._ensure_base()
            location = self.resolve_location(request.book_id, request.start_edit_id, request.end_edit_id)
            if location is None or location.chapter_index != request.chapter_index:
                raise ValueError("批注范围必须按正文顺序位于同一本书的同一章节，且段落仍然存在。")
            start = self._base_by_id[request.start_edit_id]
            end = self._base_by_id[request.end_edit_id]
            start_text = self._texts.get(request.start_edit_id, start.text)
            end_text = self._texts.get(request.end_edit_id, end.text)
            if request.start_offset > len(start_text) or request.end_offset > len(end_text):
                raise ValueError("批注范围超出了当前段落的文本长度。")
            if request.start_edit_id == request.end_edit_id and request.start_offset > request.end_offset:
                raise ValueError("批注起点不能位于终点之后。")
            annotation = ReaderAnnotation(**request.model_dump())
            self._set_annotation(annotation)
            self._undo.append(UndoEntry(label="添加批注", text_before={}, deleted_before={}, annotations_before={annotation.annotation_id: None}))
            return annotation, self.state()

    def update_annotation(self, book_id: str, annotation_id: str, payload: AnnotationUpdateInput) -> tuple[ReaderAnnotation, EditorState]:
        with self._lock:
            self._ensure_base()
            previous = self._annotations[annotation_id]
            if previous.book_id != book_id:
                raise KeyError(annotation_id)
            updates = {key: value for key, value in payload.model_dump(exclude_none=True).items() if getattr(previous, key) != value}
            if not updates:
                return previous, self.state()
            annotation = previous.model_copy(update={**updates, "updated_at": utc_now_iso()})
            self._set_annotation(annotation)
            self._undo.append(UndoEntry(label="修改批注", text_before={}, deleted_before={}, annotations_before={annotation_id: previous}))
            return annotation, self.state()

    def delete_annotation(self, annotation_id: str, *, book_id: str | None = None) -> EditorState:
        with self._lock:
            self._ensure_base()
            if book_id is not None and self._annotations[annotation_id].book_id != book_id:
                raise KeyError(annotation_id)
            annotation = self._remove_annotation(annotation_id)
            self._undo.append(UndoEntry(label="删除批注", text_before={}, deleted_before={}, annotations_before={annotation_id: annotation}))
            return self.state()

    def add_bookmark(self, book_id: str, payload: BookmarkCreateInput) -> tuple[ReaderBookmark, EditorState]:
        with self._lock:
            self._ensure_base()
            location = self.resolve_location(book_id, payload.edit_id)
            if location is None or location.chapter_index != payload.chapter_index:
                raise ValueError("书签必须指向当前书籍中仍然存在的章节和段落。")
            bookmark = ReaderBookmark(book_id=book_id, **payload.model_dump())
            self._set_bookmark(bookmark)
            self._undo.append(UndoEntry(label="添加书签", text_before={}, deleted_before={}, bookmarks_before={bookmark.bookmark_id: None}))
            return bookmark, self.state()

    def delete_bookmark(self, book_id: str, bookmark_id: str) -> EditorState:
        with self._lock:
            self._ensure_base()
            if self._bookmarks[bookmark_id].book_id != book_id:
                raise KeyError(bookmark_id)
            bookmark = self._remove_bookmark(bookmark_id)
            self._undo.append(UndoEntry(label="删除书签", text_before={}, deleted_before={}, bookmarks_before={bookmark_id: bookmark}))
            return self.state()

    def delete_chapter(self, book_id: str, chapter_index: int) -> EditorState:
        """Delete every paragraph in one chapter as a single undoable operation."""

        with self._lock:
            self._ensure_base()
            records = [
                record
                for record in self._base_by_id.values()
                if record.book_id == book_id and record.chapter_index == chapter_index
            ]
            if not records or all(reader_edit_id(record) in self._deleted for record in records):
                raise KeyError(f"{book_id}:{chapter_index}")

            text_before: dict[str, str | None] = {}
            deleted_before: dict[str, bool] = {}
            bookmarks_before: dict[str, ReaderBookmark | None] = {}
            for record in records:
                edit_id = reader_edit_id(record)
                text_before[edit_id] = self._texts.get(edit_id)
                deleted_before[edit_id] = edit_id in self._deleted
                self._texts.pop(edit_id, None)
                self._deleted.add(edit_id)
                for bookmark_id in list(self._bookmarks_by_edit_id.get(edit_id, {})):
                    bookmarks_before[bookmark_id] = self._remove_bookmark(bookmark_id)

            annotations_before = dict(self._annotations_by_chapter.get((book_id, chapter_index), {}))
            for annotation_id in annotations_before:
                self._remove_annotation(annotation_id)

            chapter_title = records[0].chapter_title.strip() or f"第 {chapter_index + 1} 章"
            self._undo.append(
                UndoEntry(
                    label=f"删除章节：{chapter_title}",
                    text_before=text_before,
                    deleted_before=deleted_before,
                    annotations_before=annotations_before,
                    bookmarks_before=bookmarks_before,
                )
            )
            self.reader.invalidate_effective({book_id})
            return self.state()

    def rename_chapter(self, book_id: str, chapter_index: int, title: str) -> EditorState:
        with self._lock:
            self._ensure_base()
            key = (book_id, chapter_index)
            records = [
                record
                for record in self._base_by_id.values()
                if record.book_id == book_id and record.chapter_index == chapter_index
            ]
            if not records or all(reader_edit_id(record) in self._deleted for record in records):
                raise KeyError(f"{book_id}:{chapter_index}")
            next_title = title.strip()
            if not next_title:
                raise ValueError("章节名称不能为空。")

            previous = self._chapter_titles.get(key)
            base_title = self._chapter_base_titles.get(key, records[0].chapter_title)
            if next_title == (previous or base_title):
                return self.state()
            if next_title == base_title:
                self._chapter_titles.pop(key, None)
            else:
                self._chapter_titles[key] = next_title
            self._undo.append(
                UndoEntry(
                    label=f"修改章节名称：{next_title}",
                    text_before={},
                    deleted_before={},
                    chapter_titles_before={key: previous},
                )
            )
            self.reader.invalidate_effective({book_id})
            return self.state()

    def undo(self) -> EditorState:
        with self._lock:
            if not self._undo:
                return self.state()
            entry = self._undo.pop()
            for edit_id, previous in entry.text_before.items():
                if previous is None:
                    self._texts.pop(edit_id, None)
                else:
                    self._texts[edit_id] = previous
            for edit_id, was_deleted in entry.deleted_before.items():
                if was_deleted:
                    self._deleted.add(edit_id)
                else:
                    self._deleted.discard(edit_id)
            if entry.annotations_before is not None:
                for annotation_id, previous in entry.annotations_before.items():
                    if previous is None:
                        self._remove_annotation(annotation_id)
                    else:
                        self._set_annotation(previous)
            if entry.bookmarks_before is not None:
                for bookmark_id, previous in entry.bookmarks_before.items():
                    if previous is None:
                        self._remove_bookmark(bookmark_id)
                    else:
                        self._set_bookmark(previous)
            if entry.chapter_titles_before is not None:
                for key, previous in entry.chapter_titles_before.items():
                    if previous is None:
                        self._chapter_titles.pop(key, None)
                    else:
                        self._chapter_titles[key] = previous
            affected_book_ids = {self._base_by_id[edit_id].book_id for edit_id in entry.text_before.keys() | entry.deleted_before.keys()}
            if entry.chapter_titles_before is not None:
                affected_book_ids.update(book_id for book_id, _chapter_index in entry.chapter_titles_before)
            self.reader.invalidate_effective(affected_book_ids)
            return self.state()

    def save(self) -> EditorState:
        with self._lock:
            self._ensure_base()
            affected_book_ids = {self._base_by_id[edit_id].book_id for edit_id in self._texts.keys() | self._deleted}
            affected_book_ids.update(book_id for book_id, _chapter_index in self._chapter_titles)
            with self.epub_editor.prepare(self.reader.records, self._texts, self._deleted, self._chapter_titles) as prepared:
                affected_book_ids.update(
                    record.book_id for previous_id, record in prepared.records_by_previous_id.items()
                    if self._base_by_id[previous_id].book_id in affected_book_ids
                )
                annotations: list[ReaderAnnotation] = []
                for annotation in self._annotations.values():
                    start = prepared.records_by_previous_id.get(annotation.start_edit_id)
                    end = prepared.records_by_previous_id.get(annotation.end_edit_id)
                    if start is None or end is None:
                        annotations.append(annotation)
                        continue
                    updates = {
                        "book_id": start.book_id,
                        "chapter_index": start.chapter_index,
                        "start_edit_id": reader_edit_id(start),
                        "end_edit_id": reader_edit_id(end),
                    }
                    annotations.append(
                        annotation if all(getattr(annotation, key) == value for key, value in updates.items())
                        else annotation.model_copy(update=updates)
                    )
                bookmarks: list[ReaderBookmark] = []
                for bookmark in self._bookmarks.values():
                    record = prepared.records_by_previous_id.get(bookmark.edit_id)
                    if record is None:
                        bookmarks.append(bookmark)
                        continue
                    updates = {
                        "book_id": record.book_id,
                        "chapter_index": record.chapter_index,
                        "edit_id": reader_edit_id(record),
                    }
                    bookmarks.append(
                        bookmark if all(getattr(bookmark, key) == value for key, value in updates.items())
                        else bookmark.model_copy(update=updates)
                    )
                temporary_files: list[Path] = []
                try:
                    reader_temporary = self._write_reader_jsonl(prepared.records)
                    temporary_files.append(reader_temporary)
                    annotations_temporary = self._write_annotations(annotations)
                    temporary_files.append(annotations_temporary)
                    bookmarks_temporary = self._write_bookmarks(bookmarks)
                    temporary_files.append(bookmarks_temporary)
                    replace_saved_files({
                        **prepared.files,
                        self.reader.reader_path: reader_temporary,
                        self.annotation_path: annotations_temporary,
                        self.bookmark_path: bookmarks_temporary,
                    })
                finally:
                    for temporary in temporary_files:
                        temporary.unlink(missing_ok=True)
                saved_records = prepared.records
            self.reader.replace_records(saved_records, affected_book_ids=affected_book_ids)
            self._texts.clear()
            self._deleted.clear()
            self._chapter_titles.clear()
            self._annotations = {item.annotation_id: item for item in annotations}
            self._bookmarks = {item.bookmark_id: item for item in bookmarks}
            self._undo.clear()
            self.rebase(affected_book_ids)
            self._saved_signature = self._signature()
            return self.state()

    def state(self) -> EditorState:
        with self._lock:
            return EditorState(
                dirty=self._signature() != self._saved_signature,
                undo_count=len(self._undo),
                last_operation=self._undo[-1].label if self._undo else "",
            )

    @property
    def has_unsaved_changes(self) -> bool:
        return self.state().dirty

    def _ensure_base(self) -> None:
        if not self._base_by_id:
            self.rebase()
        if not self._saved_signature:
            self._annotations = {item.annotation_id: item for item in self._load_annotations()}
            self._bookmarks = {item.bookmark_id: item for item in self._load_bookmarks()}
            self._rebuild_annotation_index()
            self._rebuild_bookmark_index()
            self._saved_signature = self._signature()

    def _write_reader_jsonl(self, records: list[ReaderContentRecord]) -> Path:
        output = self.reader.reader_path
        output.parent.mkdir(parents=True, exist_ok=True)
        with NamedTemporaryFile(mode="w", encoding="utf-8", dir=output.parent, prefix=f".{output.name}.", suffix=".tmp", delete=False) as file:
            temporary = Path(file.name)
            try:
                for record in records:
                    file.write(json.dumps(record.model_dump(mode="json"), ensure_ascii=False) + "\n")
            except Exception:
                file.close()
                temporary.unlink(missing_ok=True)
                raise
        return temporary

    def _load_annotations(self) -> list[ReaderAnnotation]:
        if not self.annotation_path.exists():
            return []
        data = json.loads(self.annotation_path.read_text(encoding="utf-8"))
        return [ReaderAnnotation(**item) for item in data]

    def _write_annotations(self, annotations: list[ReaderAnnotation]) -> Path:
        return self._write_note_json(self.annotation_path, annotations)

    def _load_bookmarks(self) -> list[ReaderBookmark]:
        if not self.bookmark_path.exists():
            return []
        data = json.loads(self.bookmark_path.read_text(encoding="utf-8"))
        return [ReaderBookmark(**item) for item in data]

    def _write_bookmarks(self, bookmarks: list[ReaderBookmark]) -> Path:
        return self._write_note_json(self.bookmark_path, bookmarks)

    @staticmethod
    def _write_note_json(output: Path, notes: list[ReaderAnnotation] | list[ReaderBookmark]) -> Path:
        output.parent.mkdir(parents=True, exist_ok=True)
        with NamedTemporaryFile(mode="w", encoding="utf-8", dir=output.parent, prefix=f".{output.name}.", suffix=".tmp", delete=False) as file:
            temporary = Path(file.name)
            try:
                json.dump([item.model_dump(mode="json") for item in notes], file, ensure_ascii=False, indent=2)
            except Exception:
                file.close()
                temporary.unlink(missing_ok=True)
                raise
        return temporary

    def _signature(self) -> str:
        payload = {
            "texts": sorted(self._texts.items()),
            "deleted": sorted(self._deleted),
            "chapter_titles": sorted((book_id, chapter_index, title) for (book_id, chapter_index), title in self._chapter_titles.items()),
            "annotations": [self._annotations[key].model_dump(mode="json") for key in sorted(self._annotations)],
            "bookmarks": [self._bookmarks[key].model_dump(mode="json") for key in sorted(self._bookmarks)],
        }
        encoded = json.dumps(payload, ensure_ascii=False, sort_keys=True).encode("utf-8")
        return hashlib.sha256(encoded).hexdigest()

    def _adjust_annotations(
        self,
        base: ReaderContentRecord,
        old_text: str,
        new_text: str,
        deleted: bool,
    ) -> dict[str, ReaderAnnotation]:
        edit_id = reader_edit_id(base)
        affected = self.annotations_for_record(base)
        if not affected:
            return {}
        before = {item.annotation_id: item.model_copy(deep=True) for item in affected}
        if deleted:
            for annotation in affected:
                self._remove_annotation(annotation.annotation_id)
            return before

        prefix = common_prefix_length(old_text, new_text)
        suffix = common_suffix_length(old_text[prefix:], new_text[prefix:])
        old_tail = len(old_text) - suffix
        delta = len(new_text) - len(old_text)

        def shift(offset: int) -> int:
            if offset <= prefix:
                return offset
            if offset >= old_tail:
                return max(0, offset + delta)
            return min(len(new_text), prefix)

        for annotation in affected:
            start = min(shift(annotation.start_offset), len(new_text)) if annotation.start_edit_id == edit_id else annotation.start_offset
            end = min(shift(annotation.end_offset), len(new_text)) if annotation.end_edit_id == edit_id else annotation.end_offset
            selected_text = annotation.selected_text
            if annotation.start_edit_id == edit_id == annotation.end_edit_id:
                end = max(start, end)
                selected_text = new_text[start:end]
            self._set_annotation(
                annotation.model_copy(
                    update={
                        "start_offset": start,
                        "end_offset": end,
                        "selected_text": selected_text,
                        "updated_at": utc_now_iso(),
                    }
                )
            )
        return before


def common_prefix_length(left: str, right: str) -> int:
    index = 0
    while index < min(len(left), len(right)) and left[index] == right[index]:
        index += 1
    return index


def common_suffix_length(left: str, right: str) -> int:
    index = 0
    while index < min(len(left), len(right)) and left[-index - 1] == right[-index - 1]:
        index += 1
    return index
