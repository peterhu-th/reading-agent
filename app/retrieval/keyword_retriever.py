import json
import re
from functools import cached_property
from pathlib import Path

from rank_bm25 import BM25Okapi

from app.config import get_settings
from app.models.schemas import RetrievedChunk, TextChunk
from app.retrieval.vector_retriever import MetadataValue


WORD_PATTERN = re.compile(r"[A-Za-z0-9]+")


def tokenize(text: str) -> list[str]:
    """Tokenize Chinese and Latin text without external word segmentation."""
    text = text.lower()
    tokens = WORD_PATTERN.findall(text)
    chinese_chars = [char for char in text if "\u4e00" <= char <= "\u9fff"]
    tokens.extend(chinese_chars)
    tokens.extend(
        "".join(chinese_chars[index : index + 2])
        for index in range(max(0, len(chinese_chars) - 1))
    )
    return [token for token in tokens if token.strip()]


def metadata_matches(chunk: TextChunk, metadata_filter: dict[str, MetadataValue] | None) -> bool:
    if not metadata_filter:
        return True

    for key, expected in metadata_filter.items():
        raw_actual = getattr(chunk, key, "")
        values = expected if isinstance(expected, list) else [expected]
        if key in {"chapter_index", "chunk_index"}:
            if values and raw_actual not in values:
                return False
            continue
        actual = str(raw_actual or "")
        if values and not any(str(value) and str(value) in actual for value in values):
            return False
    return True


class KeywordRetriever:
    """Local BM25 retriever over persisted chunk JSONL."""

    def __init__(self, chunks_path: str | None = None) -> None:
        settings = get_settings()
        self.chunks_path = chunks_path or settings.CHUNKS_JSONL_PATH

    @cached_property
    def chunks(self) -> list[TextChunk]:
        path = Path(self.chunks_path)
        if not path.exists():
            return []

        chunks: list[TextChunk] = []
        with path.open("r", encoding="utf-8") as file:
            for line in file:
                line = line.strip()
                if line:
                    chunks.append(TextChunk(**json.loads(line)))
        return chunks

    @cached_property
    def corpus_tokens(self) -> list[list[str]]:
        return [tokenize(chunk.text) for chunk in self.chunks]

    @cached_property
    def bm25(self) -> BM25Okapi | None:
        if not self.corpus_tokens:
            return None
        return BM25Okapi(self.corpus_tokens)

    def search(
        self,
        query: str,
        top_k: int = 5,
        metadata_filter: dict[str, MetadataValue] | None = None,
    ) -> list[RetrievedChunk]:
        query_tokens = tokenize(query)
        if not query_tokens or not self.bm25:
            return []

        scores = self.bm25.get_scores(query_tokens)
        ranked = sorted(
            enumerate(scores),
            key=lambda item: item[1],
            reverse=True,
        )

        results: list[RetrievedChunk] = []
        for index, score in ranked:
            if score <= 0:
                break
            chunk = self.chunks[index]
            if not metadata_matches(chunk, metadata_filter):
                continue
            results.append(
                RetrievedChunk(
                    chunk=chunk,
                    score=float(score),
                    keyword_score=float(score),
                    matched_queries=[query],
                )
            )
            if len(results) >= top_k:
                break
        return results
