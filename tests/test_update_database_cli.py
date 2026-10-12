import json
import os
import subprocess
import sys
from pathlib import Path

from ebooklib import epub


def test_real_pipeline_second_process_skips_all_stages_and_removal_clears_indexes(tmp_path):
    raw = tmp_path / "epub"
    raw.mkdir()
    source = raw / "book.epub"
    book = epub.EpubBook()
    book.set_identifier("incremental-cli-test")
    book.set_title("旅途中的思考")
    book.set_language("zh")
    chapter = epub.EpubHtml(uid="chapter", file_name="chapter.xhtml", title="第一章")
    chapter.content = "<html><body><h1>第一章</h1><p>" + "他在漫长的旅途中见过许多人，经历许多不同的事情，渐渐理解了生活与故乡的意义。" * 40 + "</p></body></html>"
    book.add_item(chapter)
    book.add_item(epub.EpubNcx())
    book.add_item(epub.EpubNav())
    book.toc = (epub.Link("chapter.xhtml", "第一章", "chapter-link"),)
    book.spine = ["nav", chapter]
    epub.write_epub(str(source), book)

    environment = dict(os.environ)
    environment.update(
        OPENAI_API_KEY="offline-test", OPENAI_BASE_URL="http://127.0.0.1:1/v1",
        EMBEDDING_BACKEND="local-hash", RERANK_BACKEND="lightweight", SUMMARY_BACKEND="extractive",
        RAW_EPUB_DIR=str(raw), READER_JSONL_PATH=str(tmp_path / "reader.jsonl"),
        BOOKS_JSONL_PATH=str(tmp_path / "books.jsonl"), CHUNKS_JSONL_PATH=str(tmp_path / "chunks.jsonl"),
        SUMMARIES_JSONL_PATH=str(tmp_path / "summaries.jsonl"), VECTOR_DB_PATH=str(tmp_path / "chroma"),
        CHROMA_COLLECTION="cli_chunks", SUMMARY_CHROMA_COLLECTION="cli_summaries", PYTHONUTF8="1",
    )
    root = Path(__file__).resolve().parents[1]
    command = [sys.executable, str(root / "scripts" / "update_database.py")]

    def run_update():
        result = subprocess.run(command, cwd=root, env=environment, text=True, encoding="utf-8",
                                capture_output=True, timeout=90)
        assert result.returncode == 0, result.stdout + result.stderr
        assert "XMLParsedAsHTMLWarning" not in result.stderr
        assert "Building prefix dict" not in result.stderr
        assert len(result.stdout.splitlines()) <= 6
        return result.stdout

    first = run_update()
    assert "解析 1 本" in first
    assert "摘要向量：更新" in first
    outputs = list(tmp_path.glob("*.json*"))
    stamps = {path: path.stat().st_mtime_ns for path in outputs}
    second = run_update()
    assert "跳过全部处理" in second
    assert {path: path.stat().st_mtime_ns for path in outputs} == stamps

    source.unlink()
    third = run_update()
    assert "移除 1 本" in third
    assert Path(environment["CHUNKS_JSONL_PATH"]).read_text(encoding="utf-8") == ""
    assert Path(environment["SUMMARIES_JSONL_PATH"]).read_text(encoding="utf-8") == ""
    assert "跳过全部处理" in run_update()

    # Read only the isolated database created by the subprocesses.
    import chromadb

    client = chromadb.PersistentClient(path=environment["VECTOR_DB_PATH"])
    assert client.get_collection("cli_chunks").count() == 0
    assert client.get_collection("cli_summaries").count() == 0
    state = json.loads((tmp_path / "database_update_state.json").read_text(encoding="utf-8"))
    assert state["summaries"]["output"]["size"] == 0
