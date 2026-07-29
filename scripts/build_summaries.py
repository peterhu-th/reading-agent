import argparse
import hashlib
import json
import re
import sys
from collections import Counter, defaultdict
from pathlib import Path

import httpx
from langchain_openai import ChatOpenAI

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.config import get_settings
from app.ingestion.build_index import load_chunks
from app.ingestion.normalize import is_retrievable_paragraph, normalize_whitespace
from app.models.schemas import SummaryRecord, TextChunk
from app.retrieval.keyword_retriever import tokenize


NON_CONTENT_MARKERS = (
    "版权",
    "目录",
    "出版",
    "ISBN",
    "献给",
    "译者序",
    "广告",
    "募捐",
    "VeryCD",
    "新浪爱问",
    "Scatkevin",
    "制作者",
    "原PDF",
    "OCR识别",
)
SENTENCE_PATTERN = re.compile(r"[^。！？!?；;]{18,220}[。！？!?；;]?")


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--dry-run", action="store_true", help="Show pending summaries without generating them.")
    parser.add_argument("--limit", type=int, default=0, help="Maximum changed records to generate.")
    parser.add_argument(
        "--backend",
        choices=["extractive", "llm"],
        default="",
        help="Summary backend. Defaults to SUMMARY_BACKEND from .env.",
    )
    parser.add_argument("--progress-every", type=int, default=50, help="Print progress every N records.")
    args = parser.parse_args()

    settings = get_settings()
    backend = args.backend or settings.SUMMARY_BACKEND
    chunks = load_chunks(settings.CHUNKS_JSONL_PATH)
    chunks_by_id = {chunk.chunk_id: chunk for chunk in chunks}
    existing = load_existing(settings.SUMMARIES_JSONL_PATH)
    current_records = build_current_records(chunks, backend)
    pending = [
        record
        for record in current_records
        if should_regenerate(record, existing.get(record.summary_id))
    ]

    if args.limit > 0:
        pending = pending[: args.limit]

    print(f"需要生成或更新 {len(pending)} 条摘要，backend={backend}。")
    if args.dry_run:
        for record in pending[:20]:
            print(f"- {record.summary_id} | {record.title} | {record.chapter_title or record.summary_type}")
        return

    llm = make_llm() if backend == "llm" else None
    output_path = Path(settings.SUMMARIES_JSONL_PATH)
    output_path.parent.mkdir(parents=True, exist_ok=True)
    pending_ids = {item.summary_id for item in pending}
    current_ids = {item.summary_id for item in current_records}
    stale_count = sum(1 for record in existing.values() if record.summary_id not in current_ids)
    kept = [
        record
        for record in existing.values()
        if record.summary_id in current_ids and record.summary_id not in pending_ids
    ]
    generated: list[SummaryRecord] = []

    if not pending and stale_count == 0:
        return
    if stale_count:
        print(f"清理 {stale_count} 条过期摘要。")

    for index, record in enumerate(pending, start=1):
        if backend == "llm" and llm is not None:
            prompt = build_prompt(record, chunks_by_id, settings.SUMMARY_MAX_SOURCE_CHARS)
            response = llm.invoke(prompt)
            summary_text = normalize_summary_text(str(response.content))
        else:
            summary_text = build_extractive_summary(record, chunks_by_id)
        generated.append(record.model_copy(update={"text": summary_text}))
        if index == 1 or index == len(pending) or index % max(args.progress_every, 1) == 0:
            print(f"[{index}/{len(pending)}] 已生成：{record.title} / {record.chapter_title or record.summary_type}")

    with output_path.open("w", encoding="utf-8") as file:
        for record in kept + generated:
            file.write(json.dumps(record.model_dump(), ensure_ascii=False) + "\n")


def make_llm() -> ChatOpenAI:
    settings = get_settings()
    return ChatOpenAI(
        model=settings.SUMMARY_MODEL,
        api_key=settings.OPENAI_API_KEY,
        base_url=settings.OPENAI_BASE_URL,
        temperature=0,
        http_client=httpx.Client(trust_env=False),
        http_socket_options=(),
    )


def load_existing(path: str) -> dict[str, SummaryRecord]:
    input_path = Path(path)
    if not input_path.exists():
        return {}
    records: dict[str, SummaryRecord] = {}
    with input_path.open("r", encoding="utf-8") as file:
        for line in file:
            line = line.strip()
            if line:
                record = SummaryRecord(**json.loads(line))
                records[record.summary_id] = record
    return records


def build_current_records(chunks: list[TextChunk], backend: str) -> list[SummaryRecord]:
    settings = get_settings()
    prompt_version = f"{settings.SUMMARY_PROMPT_VERSION}:{backend}"
    grouped: dict[tuple[str, int], list[TextChunk]] = defaultdict(list)
    books: dict[str, list[TextChunk]] = defaultdict(list)
    for chunk in chunks:
        if is_content_chunk(chunk):
            grouped[(chunk.book_id, chunk.chapter_index)].append(chunk)
            books[chunk.book_id].append(chunk)

    records: list[SummaryRecord] = []
    for (_book_id, _chapter_index), chapter_chunks in grouped.items():
        if not collect_sentences(chapter_chunks):
            continue
        record = make_record("chapter", chapter_chunks, prompt_version)
        records.append(record)

    for _book_id, book_chunks in books.items():
        sampled = sample_book_chunks(book_chunks)
        if not collect_sentences(sampled):
            continue
        record = make_record("book", sampled, prompt_version)
        record = record.model_copy(update={"source_hash": hash_chunks(book_chunks)})
        records.append(record)
    return records


def should_regenerate(record: SummaryRecord, old: SummaryRecord | None) -> bool:
    return (
        old is None
        or old.source_hash != record.source_hash
        or old.prompt_version != record.prompt_version
        or looks_like_truncated_source(old.text)
    )


def make_record(summary_type: str, chunks: list[TextChunk], prompt_version: str) -> SummaryRecord:
    settings = get_settings()
    first = chunks[0]
    chapter_index = first.chapter_index if summary_type == "chapter" else None
    chapter_title = first.chapter_title if summary_type == "chapter" else ""
    suffix = f"chapter:{chapter_index}" if summary_type == "chapter" else "book"
    source_chunk_ids = [chunk.chunk_id for chunk in chunks]
    return SummaryRecord(
        summary_id=f"{first.book_id}:{suffix}",
        summary_type=summary_type,
        book_id=first.book_id,
        title=first.title,
        author=first.author,
        chapter_index=chapter_index,
        chapter_title=chapter_title,
        source_chunk_ids=source_chunk_ids,
        source_hash=hash_chunks(chunks),
        summary_model=settings.SUMMARY_MODEL,
        prompt_version=prompt_version,
        text="待生成",
    )


def build_prompt(
    record: SummaryRecord,
    chunks_by_id: dict[str, TextChunk],
    max_chars: int,
) -> str:
    source_text = "\n\n".join(
        chunks_by_id[chunk_id].text
        for chunk_id in record.source_chunk_ids
        if chunk_id in chunks_by_id
    )
    target = get_target_chars(record)
    return (
        "你是中文阅读助手。请根据下面的书籍正文生成真正的摘要，不要摘抄原文片段。\n"
        f"书名：{record.title}\n作者：{record.author or 'unknown'}\n"
        f"摘要类型：{record.summary_type}\n章节：{record.chapter_title or '全书'}\n"
        f"目标长度：约 {target} 个中文字符。\n"
        "要求：概括主要情节、人物关系、论证推进或主题变化；不要输出内部 ID；不要添加正文外信息。\n"
        f"来源正文：\n{source_text[:max_chars]}"
    )


def build_extractive_summary(
    record: SummaryRecord,
    chunks_by_id: dict[str, TextChunk],
) -> str:
    chunks = [chunks_by_id[chunk_id] for chunk_id in record.source_chunk_ids if chunk_id in chunks_by_id]
    sentences = collect_sentences(chunks)
    if not sentences:
        return f"{record.title}：暂无可用正文。"

    target = get_target_chars(record)
    selected = select_summary_sentences(sentences, record, target)
    heading = f"{record.title}"
    if record.summary_type == "chapter" and record.chapter_title:
        heading += f"｜{record.chapter_title}"
    return f"{heading}：本段内容主要包括：" + "；".join(selected)


def collect_sentences(chunks: list[TextChunk]) -> list[str]:
    sentences: list[str] = []
    for chunk in chunks:
        text = normalize_summary_text(chunk.text)
        if not is_content_text(text):
            continue
        for match in SENTENCE_PATTERN.finditer(text):
            sentence = normalize_summary_text(match.group(0))
            if is_good_summary_sentence(sentence):
                sentences.append(sentence.rstrip("；;。"))
    return unique_strings(sentences)


def select_summary_sentences(
    sentences: list[str],
    record: SummaryRecord,
    target_chars: int,
) -> list[str]:
    if len(sentences) <= 3:
        return sentences

    query_tokens = set(tokenize(" ".join([record.title, record.chapter_title])))
    global_tokens = Counter(token for sentence in sentences for token in tokenize(sentence))
    scored: list[tuple[float, int, str]] = []
    total = len(sentences)
    for index, sentence in enumerate(sentences):
        tokens = tokenize(sentence)
        token_score = sum(global_tokens[token] for token in set(tokens)) / max(len(set(tokens)), 1)
        title_score = len(query_tokens & set(tokens)) * 1.5
        length_score = 1.0 if 35 <= len(sentence) <= 160 else 0.4
        position_score = 1.0 if index < 3 else 0.6 if index > total - 4 else 0.3
        scored.append((token_score + title_score + length_score + position_score, index, sentence))

    scored.sort(key=lambda item: item[0], reverse=True)
    chosen = sorted(scored[: max(3, min(6, target_chars // 90))], key=lambda item: item[1])
    result: list[str] = []
    current_chars = 0
    for _score, _index, sentence in chosen:
        if current_chars and current_chars + len(sentence) > target_chars:
            continue
        result.append(sentence)
        current_chars += len(sentence)
    return result or [sentences[0]]


def get_target_chars(record: SummaryRecord) -> int:
    settings = get_settings()
    return (
        settings.SUMMARY_BOOK_TARGET_CHARS
        if record.summary_type == "book"
        else settings.SUMMARY_CHAPTER_TARGET_CHARS
    )


def sample_book_chunks(chunks: list[TextChunk], max_chunks: int = 80) -> list[TextChunk]:
    if len(chunks) <= max_chunks:
        return chunks
    step = max(1, len(chunks) // max_chunks)
    sampled = chunks[::step][:max_chunks]
    if chunks[-1] not in sampled:
        sampled.append(chunks[-1])
    return sampled


def is_content_chunk(chunk: TextChunk) -> bool:
    return is_content_text(chunk.text)


def is_content_text(text: str) -> bool:
    clean = normalize_summary_text(text)
    if len(clean) < 40:
        return False
    if not is_retrievable_paragraph(clean[:240]):
        return False
    return not any(marker.lower() in clean[:400].lower() for marker in NON_CONTENT_MARKERS)


def is_good_summary_sentence(sentence: str) -> bool:
    if len(sentence) < 18 or len(sentence) > 220:
        return False
    if not is_content_text(sentence):
        return False
    if re.search(r"https?://|www\.|@|VeryCD|ISBN", sentence, flags=re.IGNORECASE):
        return False
    return True


def looks_like_truncated_source(text: str) -> bool:
    if "本段内容主要包括：" in text:
        return False
    if "暂无可用正文" in text:
        return True
    if len(text) < 80:
        return True
    return text.rstrip().endswith(("...", "……")) or " http://" in text or "VeryCD" in text


def normalize_summary_text(text: str) -> str:
    return normalize_whitespace(text.replace("\u3000", " "))


def unique_strings(values: list[str]) -> list[str]:
    result: list[str] = []
    for value in values:
        if value and value not in result:
            result.append(value)
    return result


def hash_chunks(chunks: list[TextChunk]) -> str:
    digest = hashlib.sha256()
    for chunk in chunks:
        digest.update(chunk.chunk_id.encode("utf-8"))
        digest.update(chunk.text.encode("utf-8"))
    return digest.hexdigest()


if __name__ == "__main__":
    main()
