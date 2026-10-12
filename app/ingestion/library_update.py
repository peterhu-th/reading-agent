from __future__ import annotations

import json
from collections import defaultdict
from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path
from tempfile import NamedTemporaryFile
from typing import TypeVar

from pydantic import BaseModel

from app.config import Settings
from app.ingestion.chunking import chunk_paragraphs
from app.ingestion.load_epub import reader_records_to_paragraphs
from app.ingestion.reader_epub import load_epub_for_reader
from app.models.schemas import BookParagraph, ReaderContentRecord, TextChunk
from app.services.epub_editor import replace_saved_files


@dataclass
class LibraryUpdateStats:
    total_books: int = 0
    parsed_books: int = 0
    reused_books: int = 0
    deleted_books: int = 0
    projected_books: int = 0
    rechunked_books: int = 0
    changed_book_ids: set[str] = field(default_factory=set)
    reader_changed: bool = False
    paragraphs_changed: bool = False
    chunks_changed: bool = False


Record = TypeVar("Record", bound=BaseModel)


def update_library(
    settings: Settings,
    *,
    force: bool = False,
    progress: Callable[[str], None] | None = None,
) -> LibraryUpdateStats:
    """Parse changed EPUBs once and reuse the other books' processed records."""

    raw_dir = Path(settings.RAW_EPUB_DIR).resolve()
    if not raw_dir.is_dir():
        raise FileNotFoundError(f"EPUB 目录不存在：{raw_dir}")
    paths = sorted(raw_dir.glob("*.epub"))
    outputs = {
        "reader": Path(settings.READER_JSONL_PATH).resolve(),
        "paragraphs": Path(settings.BOOKS_JSONL_PATH).resolve(),
        "chunks": Path(settings.CHUNKS_JSONL_PATH).resolve(),
    }
    state_path = outputs["reader"].with_name("library_update_state.json")
    state = _read_state(state_path)
    previous_sources = state.get("sources", {})
    same_source_dir = state.get("raw_epub_dir") == str(raw_dir)
    source_stats = {path.name: _file_stat(path) for path in paths}
    output_stats = {name: _output_stat(path) for name, path in outputs.items()}
    previous_outputs = state.get("outputs", {})
    chunking = {"chunk_size": settings.CHUNK_SIZE, "overlap": settings.CHUNK_OVERLAP}
    changed_sources = {
        name for name, stat in source_stats.items()
        if not same_source_dir or name not in previous_sources or previous_sources[name].get("stat") != stat
    }
    deleted_sources = set(previous_sources) - set(source_stats)
    stats = LibraryUpdateStats(total_books=len(paths), deleted_books=len(deleted_sources))
    if (
        not force and same_source_dir and not changed_sources and not deleted_sources
        and state.get("chunking") == chunking
        and output_stats == previous_outputs and all(output_stats.values())
    ):
        stats.reused_books = len(paths)
        if progress:
            progress(f"书库无变化，复用 {len(paths)} 本书；阅读数据与分块文件保持不变。")
        return stats

    old_reader = _read_jsonl(outputs["reader"], ReaderContentRecord)
    old_paragraphs = _read_jsonl(outputs["paragraphs"], BookParagraph)
    old_chunks = _read_jsonl(outputs["chunks"], TextChunk)
    readers_by_source = _group(old_reader or [], lambda record: record.source_epub)
    paragraphs_by_source = _group(old_paragraphs or [], lambda record: str(Path(record.source_path).resolve()))
    old_paragraphs_by_book = _group(old_paragraphs or [], lambda record: record.book_id)
    old_chunks_by_book = _group(old_chunks or [], lambda record: record.book_id)
    reader_stamp_matches = output_stats["reader"] is not None and output_stats["reader"] == previous_outputs.get("reader")
    paragraph_stamp_matches = output_stats["paragraphs"] is not None and output_stats["paragraphs"] == previous_outputs.get("paragraphs")
    chunk_stamp_matches = output_stats["chunks"] is not None and output_stats["chunks"] == previous_outputs.get("chunks")
    all_reader: list[ReaderContentRecord] = []
    all_paragraphs: list[BookParagraph] = []
    next_sources: dict[str, dict] = {}

    for name in deleted_sources:
        stats.changed_book_ids.update(previous_sources[name].get("book_ids", []))
    for path in paths:
        previous = previous_sources.get(path.name)
        cached_reader = readers_by_source.get(path.name, [])
        # Editor saves replace the complete reader JSONL together with changed
        # EPUBs. Unchanged source files can still reuse their complete records.
        reusable_reader = (
            not force and path.name not in changed_sources and previous is not None
            and old_reader is not None
            and (reader_stamp_matches or bool(changed_sources or deleted_sources))
            and len(cached_reader) == previous.get("reader_count")
            and sorted({item.book_id for item in cached_reader}) == previous.get("book_ids")
        )
        if reusable_reader:
            records = cached_reader
            stats.reused_books += 1
        else:
            records = load_epub_for_reader(path)
            stats.parsed_books += 1
            stats.changed_book_ids.update(item.book_id for item in records)
            if previous is not None:
                stats.changed_book_ids.update(previous.get("book_ids", []))
            if progress:
                progress(f"解析 {path.name}：{len(records)} 个阅读文本块。")

        cached_paragraphs = paragraphs_by_source.get(str(path), [])
        if (
            reusable_reader and paragraph_stamp_matches and old_paragraphs is not None
            and len(cached_paragraphs) == previous.get("paragraph_count")
        ):
            paragraphs = cached_paragraphs
        else:
            paragraphs = reader_records_to_paragraphs(records, str(path))
            stats.projected_books += 1
        all_reader.extend(records)
        all_paragraphs.extend(paragraphs)
        next_sources[path.name] = {
            "stat": source_stats[path.name],
            "book_ids": sorted({item.book_id for item in records}),
            "reader_count": len(records),
            "paragraph_count": len(paragraphs),
        }

    paragraphs_by_book = _group(all_paragraphs, lambda record: record.book_id)
    all_chunks: list[TextChunk] = []
    for book_id, paragraphs in sorted(paragraphs_by_book.items()):
        if (
            not force and chunk_stamp_matches and old_chunks is not None
            and state.get("chunking") == chunking
            and paragraphs == old_paragraphs_by_book.get(book_id)
        ):
            chunks = old_chunks_by_book.get(book_id, [])
        else:
            chunks = chunk_paragraphs(paragraphs, chunk_size=settings.CHUNK_SIZE, overlap=settings.CHUNK_OVERLAP)
            stats.rechunked_books += 1
        if chunks != old_chunks_by_book.get(book_id, []):
            stats.changed_book_ids.add(book_id)
        all_chunks.extend(chunks)
    stats.changed_book_ids.update(set(old_chunks_by_book) - set(paragraphs_by_book))
    stats.reader_changed = old_reader is None or old_reader != all_reader
    stats.paragraphs_changed = old_paragraphs is None or old_paragraphs != all_paragraphs
    stats.chunks_changed = old_chunks is None or old_chunks != all_chunks
    next_state = {"raw_epub_dir": str(raw_dir), "chunking": chunking, "sources": next_sources, "outputs": {}}
    temporary_files: list[Path] = []
    try:
        staged: dict[Path, Path] = {}
        for name, records, changed in (
            ("reader", all_reader, stats.reader_changed),
            ("paragraphs", all_paragraphs, stats.paragraphs_changed),
            ("chunks", all_chunks, stats.chunks_changed),
        ):
            destination = outputs[name]
            if changed:
                temporary = _stage_jsonl(destination, records, temporary_files)
                staged[destination] = temporary
                next_state["outputs"][name] = {"path": str(destination), **_file_stat(temporary)}
            else:
                next_state["outputs"][name] = output_stats[name]
        if next_state != state:
            staged[state_path] = _stage_json(state_path, next_state, temporary_files)
        # Do not record a source version that changed while its outputs were
        # being generated; retrying will then start from the last complete run.
        if source_stats != {path.name: _file_stat(path) for path in sorted(raw_dir.glob("*.epub"))}:
            raise RuntimeError("EPUB 在数据库更新期间发生变化，请重新运行更新。")
        replace_saved_files(staged)
    finally:
        for temporary in temporary_files:
            temporary.unlink(missing_ok=True)
    if progress:
        progress(f"书库更新完成：解析 {stats.parsed_books} 本，复用 {stats.reused_books} 本，移除 {stats.deleted_books} 本。")
    return stats


def _file_stat(path: Path) -> dict[str, int] | None:
    try:
        stat = path.stat()
    except FileNotFoundError:
        return None
    return {"mtime_ns": stat.st_mtime_ns, "size": stat.st_size}


def _output_stat(path: Path) -> dict | None:
    stat = _file_stat(path)
    return {"path": str(path), **stat} if stat is not None else None


def _read_state(path: Path) -> dict:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (FileNotFoundError, ValueError):
        return {}
    return value if isinstance(value, dict) else {}


def _read_jsonl(path: Path, model: type[Record]) -> list[Record] | None:
    try:
        with path.open(encoding="utf-8") as file:
            return [model.model_validate_json(line) for line in file if line.strip()]
    except (FileNotFoundError, ValueError):
        return None


def _group(records: list[Record], key: Callable[[Record], str]) -> dict[str, list[Record]]:
    grouped: dict[str, list[Record]] = defaultdict(list)
    for record in records:
        grouped[key(record)].append(record)
    return dict(grouped)


def _stage_jsonl(destination: Path, records: list[BaseModel], temporary_files: list[Path]) -> Path:
    destination.parent.mkdir(parents=True, exist_ok=True)
    with NamedTemporaryFile(mode="w", encoding="utf-8", dir=destination.parent, prefix=f".{destination.name}.", suffix=".tmp", delete=False) as file:
        temporary = Path(file.name)
        temporary_files.append(temporary)
        for record in records:
            file.write(record.model_dump_json() + "\n")
    return temporary


def _stage_json(destination: Path, value: dict, temporary_files: list[Path]) -> Path:
    destination.parent.mkdir(parents=True, exist_ok=True)
    with NamedTemporaryFile(mode="w", encoding="utf-8", dir=destination.parent, prefix=f".{destination.name}.", suffix=".tmp", delete=False) as file:
        temporary = Path(file.name)
        temporary_files.append(temporary)
        json.dump(value, file, ensure_ascii=False, indent=2)
        file.write("\n")
    return temporary
