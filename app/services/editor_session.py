from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from pathlib import Path
from tempfile import NamedTemporaryFile
from threading import RLock

from app.config import get_settings
from app.models.schemas import (
    AnnotationCreateRequest,
    EditorState,
    ReaderAnnotation,
    ReaderContentRecord,
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
    annotations_before: list[ReaderAnnotation] | None = None
    chapter_titles_before: dict[tuple[str, int], str | None] | None = None


class EditorSessionService:
    """Service-lifetime working document plus complete undo history."""

    def __init__(self, reader: ReaderService) -> None:
        settings = get_settings()
        self.reader = reader
        self.annotation_path = Path(settings.ANNOTATIONS_JSON_PATH)
        self.epub_editor = EpubEditorService(settings.RAW_EPUB_DIR)
        self._lock = RLock()
        self._base_by_id: dict[str, ReaderContentRecord] = {}
        self._chapter_positions: dict[tuple[str, int], dict[str, int]] = {}
        self._texts: dict[str, str] = {}
        self._deleted: set[str] = set()
        self._annotations: list[ReaderAnnotation] = []
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
            self._annotations = self._load_annotations()
            self.rebase()
            self._saved_signature = self._signature()

    def rebase(self) -> None:
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
            self.reader.set_editor(self)

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
        edit_id = reader_edit_id(record)
        positions = self._chapter_positions.get((record.book_id, record.chapter_index), {})
        position = positions.get(edit_id, -1)
        result: list[ReaderAnnotation] = []
        with self._lock:
            for annotation in self._annotations:
                if annotation.book_id != record.book_id or annotation.chapter_index != record.chapter_index:
                    continue
                start = positions.get(annotation.start_edit_id, -1)
                end = positions.get(annotation.end_edit_id, -1)
                if start >= 0 and end >= start and start <= position <= end:
                    result.append(annotation)
        return result

    def apply_operation(self, request: ReaderOperationRequest) -> EditorState:
        with self._lock:
            self._ensure_base()
            text_before: dict[str, str | None] = {}
            deleted_before: dict[str, bool] = {}
            annotations_before: list[ReaderAnnotation] | None = None
            for mutation in request.mutations:
                base = self._base_by_id.get(mutation.edit_id)
                if base is None or base.book_id != request.book_id:
                    raise KeyError(mutation.edit_id)
                text_before[mutation.edit_id] = self._texts.get(mutation.edit_id)
                deleted_before[mutation.edit_id] = mutation.edit_id in self._deleted
                current_text = self._texts.get(mutation.edit_id, base.text)
                next_text = "" if mutation.deleted else mutation.text
                annotation_snapshot = annotations_before or [item.model_copy(deep=True) for item in self._annotations]
                if self._adjust_annotations(base, mutation.edit_id, current_text, next_text, mutation.deleted):
                    if annotations_before is None:
                        annotations_before = annotation_snapshot
                if mutation.deleted or not mutation.text.strip():
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
                )
            )
            self.reader.invalidate_effective()
            return self.state()

    def add_annotation(self, request: AnnotationCreateRequest) -> tuple[ReaderAnnotation, EditorState]:
        with self._lock:
            self._ensure_base()
            start = self._base_by_id.get(request.start_edit_id)
            end = self._base_by_id.get(request.end_edit_id)
            if start is None or end is None or start.book_id != request.book_id or end.book_id != request.book_id:
                raise KeyError(request.start_edit_id)
            before = [item.model_copy(deep=True) for item in self._annotations]
            annotation = ReaderAnnotation(**request.model_dump())
            self._annotations.append(annotation)
            self._undo.append(UndoEntry(label="添加批注", text_before={}, deleted_before={}, annotations_before=before))
            return annotation, self.state()

    def delete_annotation(self, annotation_id: str) -> EditorState:
        with self._lock:
            before = [item.model_copy(deep=True) for item in self._annotations]
            remaining = [item for item in self._annotations if item.annotation_id != annotation_id]
            if len(remaining) == len(self._annotations):
                raise KeyError(annotation_id)
            self._annotations = remaining
            self._undo.append(UndoEntry(label="删除批注", text_before={}, deleted_before={}, annotations_before=before))
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
            for record in records:
                edit_id = reader_edit_id(record)
                text_before[edit_id] = self._texts.get(edit_id)
                deleted_before[edit_id] = edit_id in self._deleted
                self._texts.pop(edit_id, None)
                self._deleted.add(edit_id)

            annotations_before: list[ReaderAnnotation] | None = None
            if any(item.book_id == book_id and item.chapter_index == chapter_index for item in self._annotations):
                annotations_before = [item.model_copy(deep=True) for item in self._annotations]
                self._annotations = [
                    item
                    for item in self._annotations
                    if item.book_id != book_id or item.chapter_index != chapter_index
                ]

            chapter_title = records[0].chapter_title.strip() or f"第 {chapter_index + 1} 章"
            self._undo.append(
                UndoEntry(
                    label=f"删除章节：{chapter_title}",
                    text_before=text_before,
                    deleted_before=deleted_before,
                    annotations_before=annotations_before,
                )
            )
            self.reader.invalidate_effective()
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
            self.reader.invalidate_effective()
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
                self._annotations = [item.model_copy(deep=True) for item in entry.annotations_before]
            if entry.chapter_titles_before is not None:
                for key, previous in entry.chapter_titles_before.items():
                    if previous is None:
                        self._chapter_titles.pop(key, None)
                    else:
                        self._chapter_titles[key] = previous
            self.reader.invalidate_effective()
            return self.state()

    def save(self) -> EditorState:
        with self._lock:
            self._ensure_base()
            with self.epub_editor.prepare(self.reader.records, self._texts, self._deleted, self._chapter_titles) as prepared:
                annotations: list[ReaderAnnotation] = []
                for annotation in self._annotations:
                    start = prepared.records_by_previous_id.get(annotation.start_edit_id)
                    end = prepared.records_by_previous_id.get(annotation.end_edit_id)
                    if start is None or end is None:
                        annotations.append(annotation)
                        continue
                    annotations.append(annotation.model_copy(update={
                        "book_id": start.book_id,
                        "chapter_index": start.chapter_index,
                        "start_edit_id": reader_edit_id(start),
                        "end_edit_id": reader_edit_id(end),
                    }))
                temporary_files: list[Path] = []
                try:
                    reader_temporary = self._write_reader_jsonl(prepared.records)
                    temporary_files.append(reader_temporary)
                    annotations_temporary = self._write_annotations(annotations)
                    temporary_files.append(annotations_temporary)
                    replace_saved_files({
                        **prepared.files,
                        self.reader.reader_path: reader_temporary,
                        self.annotation_path: annotations_temporary,
                    })
                finally:
                    for temporary in temporary_files:
                        temporary.unlink(missing_ok=True)
                saved_records = prepared.records
            self.reader.replace_records(saved_records)
            self._texts.clear()
            self._deleted.clear()
            self._chapter_titles.clear()
            self._annotations = annotations
            self._undo.clear()
            self.rebase()
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
            self._annotations = self._load_annotations()
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
        self.annotation_path.parent.mkdir(parents=True, exist_ok=True)
        with NamedTemporaryFile(mode="w", encoding="utf-8", dir=self.annotation_path.parent, prefix=f".{self.annotation_path.name}.", suffix=".tmp", delete=False) as file:
            temporary = Path(file.name)
            try:
                json.dump([item.model_dump(mode="json") for item in annotations], file, ensure_ascii=False, indent=2)
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
            "annotations": [item.model_dump(mode="json") for item in self._annotations],
        }
        encoded = json.dumps(payload, ensure_ascii=False, sort_keys=True).encode("utf-8")
        return hashlib.sha256(encoded).hexdigest()

    def _adjust_annotations(
        self,
        base: ReaderContentRecord,
        edit_id: str,
        old_text: str,
        new_text: str,
        deleted: bool,
    ) -> bool:
        affected = [item for item in self._annotations if self._annotation_covers(item, base, edit_id)]
        if not affected:
            return False
        if deleted:
            affected_ids = {item.annotation_id for item in affected}
            self._annotations = [item for item in self._annotations if item.annotation_id not in affected_ids]
            return True

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

        updated: list[ReaderAnnotation] = []
        affected_ids = {item.annotation_id for item in affected}
        for annotation in self._annotations:
            if annotation.annotation_id not in affected_ids:
                updated.append(annotation)
                continue
            start = min(shift(annotation.start_offset), len(new_text)) if annotation.start_edit_id == edit_id else annotation.start_offset
            end = min(shift(annotation.end_offset), len(new_text)) if annotation.end_edit_id == edit_id else annotation.end_offset
            selected_text = annotation.selected_text
            if annotation.start_edit_id == edit_id == annotation.end_edit_id:
                end = max(start, end)
                selected_text = new_text[start:end]
            updated.append(
                annotation.model_copy(
                    update={
                        "start_offset": start,
                        "end_offset": end,
                        "selected_text": selected_text,
                        "updated_at": utc_now_iso(),
                    }
                )
            )
        self._annotations = updated
        return True

    def _annotation_covers(self, annotation: ReaderAnnotation, record: ReaderContentRecord, edit_id: str) -> bool:
        if annotation.book_id != record.book_id or annotation.chapter_index != record.chapter_index:
            return False
        positions = self._chapter_positions.get((record.book_id, record.chapter_index), {})
        position = positions.get(edit_id, -1)
        start = positions.get(annotation.start_edit_id, -1)
        end = positions.get(annotation.end_edit_id, -1)
        return start >= 0 and end >= start and start <= position <= end


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
