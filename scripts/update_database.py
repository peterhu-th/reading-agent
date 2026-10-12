"""Update only changed library data and reuse models across index stages."""

from __future__ import annotations

import argparse
import json
import logging
import sys
import time
from collections.abc import Callable
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.config import Settings, get_settings
from app.ingestion.library_update import update_library


def file_state(path: str | Path) -> dict | None:
    target = Path(path).resolve()
    if not target.is_file():
        return None
    stat = target.stat()
    return {"path": str(target), "mtime_ns": stat.st_mtime_ns, "size": stat.st_size}


def vector_store_state(path: str | Path) -> dict | None:
    directory = Path(path).resolve()
    if not (directory / "chroma.sqlite3").is_file():
        return None
    # Notice deleted/replaced HNSW segments as well as collection changes.
    files = {
        str(item.relative_to(directory)): file_state(item)
        for item in sorted(directory.rglob("*"))
        if item.is_file() and not item.name.endswith("-shm")
    }
    return {"path": str(directory), "files": files}


def missing_vector_files(previous: dict | None, current: dict | None) -> bool:
    if not previous or not current or previous.get("path") != current.get("path"):
        return False
    missing = previous["files"].keys() - current["files"].keys()
    return any(not Path(name).name.startswith("chroma.sqlite3") for name in missing)


def summary_config(settings: Settings) -> dict:
    config = {
        "backend": settings.SUMMARY_BACKEND,
        "prompt_version": settings.SUMMARY_PROMPT_VERSION,
        "chapter_target_chars": settings.SUMMARY_CHAPTER_TARGET_CHARS,
        "book_target_chars": settings.SUMMARY_BOOK_TARGET_CHARS,
    }
    if settings.SUMMARY_BACKEND == "llm":
        config.update(model=settings.SUMMARY_MODEL, base_url=settings.OPENAI_BASE_URL,
                      max_source_chars=settings.SUMMARY_MAX_SOURCE_CHARS)
    return config


class DatabaseUpdater:
    def __init__(
        self,
        *,
        force: bool = False,
        verbose: bool = False,
        output: Callable[[str], None] | None = None,
    ) -> None:
        self.settings = get_settings()
        self.force = force
        self.verbose = verbose
        self.output = output or (lambda message: print(message, flush=True))
        self.state_path = Path(self.settings.READER_JSONL_PATH).parent / "database_update_state.json"
        self.current_stage = "检查更新"

    def _load_state(self) -> dict:
        if not self.state_path.exists():
            return {}
        try:
            return json.loads(self.state_path.read_text(encoding="utf-8"))
        except json.JSONDecodeError:
            self.output("更新记录无法读取，将重新检查各阶段。")
            return {}

    def _save_state(self, state: dict) -> None:
        self.state_path.parent.mkdir(parents=True, exist_ok=True)
        text = json.dumps(state, ensure_ascii=False, indent=2)
        if self.state_path.exists() and self.state_path.read_text(encoding="utf-8") == text:
            return
        temporary = self.state_path.with_suffix(".json.tmp")
        try:
            temporary.write_text(text, encoding="utf-8")
            temporary.replace(self.state_path)
        finally:
            temporary.unlink(missing_ok=True)

    def _summary_update(self, *, changed_book_ids: set[str] | None, force: bool):
        from scripts.build_summaries import update_summaries

        if not self.verbose:
            logging.getLogger("jieba").setLevel(logging.WARNING)
        return update_summaries(changed_book_ids=changed_book_ids, force=force,
                                progress=self.output if self.verbose else None)

    def _index_chunks(self, *, force_reset: bool = False):
        from app.ingestion.build_index import rebuild_index_with_stats

        return rebuild_index_with_stats(progress=self.output if self.verbose else None, force_reset=force_reset)

    def _index_summaries(self, *, force_reset: bool = False):
        from scripts.rebuild_summary_index import rebuild_summary_index

        return rebuild_summary_index(progress=self.output if self.verbose else None, force_reset=force_reset)

    def _index_inputs(self, source: str, collection: str) -> dict:
        settings = self.settings
        backend = settings.EMBEDDING_BACKEND.strip().lower().replace("_", "-")
        return {
            "source": file_state(source), "collection": collection,
            "embedding_backend": backend,
            "embedding_model": "local-hash" if backend == "local-hash" else settings.EMBEDDING_MODEL,
            "embedding_normalize": True,
        }

    def update_all(self) -> set[str]:
        started = time.perf_counter()
        settings = self.settings
        state = self._load_state()
        previous_vectors = vector_store_state(settings.VECTOR_DB_PATH)
        vectors_unchanged = previous_vectors is not None and previous_vectors == state.get("vector_store")
        rebuilds = set(state.get("index_rebuilds", []))
        if missing_vector_files(state.get("vector_store"), previous_vectors):
            rebuilds.update(("chunk_index", "summary_index"))
        if not vectors_unchanged:
            state.pop("chunk_index", None)
            state.pop("summary_index", None)
        previous_chunks = file_state(settings.CHUNKS_JSONL_PATH)
        previous_summaries = file_state(settings.SUMMARIES_JSONL_PATH)
        completed: set[str] = set()
        self.output("检查书籍和更新配置…")

        self.current_stage = "更新阅读数据"
        library = update_library(settings, force=self.force, progress=self.output if self.verbose else None)
        if (library.parsed_books or library.projected_books or library.rechunked_books
                or library.reader_changed or library.paragraphs_changed or library.chunks_changed):
            completed.add("library")
            self.output(f"阅读数据：解析 {library.parsed_books} 本，复用 {library.reused_books} 本，"
                        f"移除 {library.deleted_books} 本，重新分块 {library.rechunked_books} 本。")
        elif self.verbose:
            self.output("阅读数据未变化。")

        configuration = summary_config(settings)
        summary_inputs = {"chunks": file_state(settings.CHUNKS_JSONL_PATH), "config": configuration}
        old_summary = state.get("summaries", {})
        summary_valid = (previous_summaries is not None
                         and old_summary.get("inputs") == summary_inputs
                         and old_summary.get("output") == previous_summaries)
        if self.force or not summary_valid:
            self.current_stage = "更新摘要"
            config_changed = bool(old_summary) and old_summary.get("inputs", {}).get("config") != configuration
            # Narrow only when the last summaries match the chunks read before
            # this ingestion. Earlier failed runs may leave other books pending.
            can_narrow = (not self.force and library.chunks_changed
                          and old_summary.get("inputs") == {"chunks": previous_chunks, "config": configuration}
                          and old_summary.get("output") == previous_summaries)
            stats = self._summary_update(changed_book_ids=library.changed_book_ids if can_narrow else None,
                                         force=config_changed)
            completed.add("summaries")
            self.output(f"摘要：更新 {stats.generated_records} 条，移除 {stats.deleted_records} 条。")
            state["summaries"] = {"inputs": summary_inputs, "output": file_state(settings.SUMMARIES_JSONL_PATH)}
            self._save_state(state)
        elif self.verbose:
            self.output("摘要未变化，跳过生成。")

        # Summary generation may take minutes. Recheck before deciding whether
        # the vector stages can use their previous successful input records.
        current_vectors = vector_store_state(settings.VECTOR_DB_PATH)
        if current_vectors != previous_vectors:
            vectors_unchanged = False
            state.pop("chunk_index", None)
            state.pop("summary_index", None)
            if missing_vector_files(previous_vectors, current_vectors):
                rebuilds.update(("chunk_index", "summary_index"))
        if rebuilds:
            self.output("检测到向量文件缺失，将重建受影响的向量索引。")

        for stage, label, source, collection, action in (
            ("chunk_index", "正文向量", settings.CHUNKS_JSONL_PATH, settings.CHROMA_COLLECTION, self._index_chunks),
            ("summary_index", "摘要向量", settings.SUMMARIES_JSONL_PATH, settings.SUMMARY_CHROMA_COLLECTION, self._index_summaries),
        ):
            inputs = self._index_inputs(source, collection)
            if not self.force and stage not in rebuilds and vectors_unchanged and state.get(stage) == inputs:
                if self.verbose:
                    self.output(f"{label}未变化，跳过同步。")
                continue
            self.current_stage = f"更新{label}"
            stats = action(force_reset=stage in rebuilds)
            completed.add(stage)
            self.output(f"{label}：更新 {stats.indexed_chunks} 条，复用 {stats.skipped_chunks} 条，移除 {stats.deleted_chunks} 条。")
            state[stage] = inputs
            rebuilds.discard(stage)
            if rebuilds:
                state["index_rebuilds"] = sorted(rebuilds)
            else:
                state.pop("index_rebuilds", None)
            state["vector_store"] = vector_store_state(settings.VECTOR_DB_PATH)
            self._save_state(state)

        elapsed = time.perf_counter() - started
        if completed:
            self.output(f"数据库更新完成，耗时 {elapsed:.1f} 秒；重启 Web 服务后载入更新。")
        else:
            self.output(f"书库和配置均未变化，已跳过全部处理，耗时 {elapsed:.1f} 秒。")
        return completed


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--force", action="store_true", help="重新解析全部 EPUB 并检查所有阶段；相同向量仍复用。")
    parser.add_argument("--verbose", action="store_true", help="显示逐书、逐批次处理和跳过详情。")
    args = parser.parse_args()
    for stream in (sys.stdout, sys.stderr):
        if hasattr(stream, "reconfigure"):
            stream.reconfigure(encoding="utf-8")
    logging.getLogger("jieba").setLevel(logging.WARNING)
    updater = DatabaseUpdater(force=args.force, verbose=args.verbose)
    try:
        updater.update_all()
    except Exception as exc:
        if args.verbose:
            raise
        print(f"数据库更新失败（{updater.current_stage}）：{exc}", file=sys.stderr, flush=True)
        raise SystemExit(1) from None


if __name__ == "__main__":
    main()
