import json
from functools import cached_property
from pathlib import Path

from app.agent.intent_analyzer import analyze_intent
from app.config import get_settings
from app.models.schemas import (
    ConversationSession,
    IntentAnalysis,
    PlannedQuery,
    RetrievalDebugInfo,
    RetrievalPlan,
    RetrievalResult,
    RetrievedChunk,
    SummaryRecord,
    TextChunk,
)
from app.retrieval.keyword_retriever import KeywordRetriever
from app.retrieval.query_planner import plan_retrieval
from app.retrieval.reranker import rerank_chunks_with_debug
from app.retrieval.summary_retriever import SummaryRetriever
from app.retrieval.vector_retriever import VectorRetriever


class EnhancedRetriever:
    """Hybrid retriever with summary guidance, rerank, and neighbor expansion."""

    def __init__(self) -> None:
        self.settings = get_settings()
        self.vector = VectorRetriever()
        self.keyword = KeywordRetriever()
        self.summary = SummaryRetriever()

    @cached_property
    def chunks(self) -> list[TextChunk]:
        path = Path(self.settings.CHUNKS_JSONL_PATH)
        if not path.exists():
            return []
        chunks: list[TextChunk] = []
        with path.open("r", encoding="utf-8") as file:
            for line in file:
                if line.strip():
                    chunks.append(TextChunk(**json.loads(line)))
        return chunks

    @cached_property
    def chunk_by_id(self) -> dict[str, TextChunk]:
        return {chunk.chunk_id: chunk for chunk in self.chunks}

    @cached_property
    def position_index(self) -> dict[tuple[str, int, int], TextChunk]:
        return {
            (chunk.book_id, chunk.chapter_index, chunk.chunk_index): chunk
            for chunk in self.chunks
        }

    def search(
        self,
        question: str,
        debug: bool = False,
        conversation: ConversationSession | None = None,
    ) -> RetrievalResult:
        intent = analyze_intent(question, conversation)
        return self.search_with_plan(intent, plan_retrieval(intent, self.settings), debug)

    def search_with_plan(
        self,
        intent: IntentAnalysis,
        plan: RetrievalPlan,
        debug: bool = False,
    ) -> RetrievalResult:
        debug_info = RetrievalDebugInfo()
        candidates = self.collect_candidates(plan)
        summaries = self.collect_summary_records(plan) if plan.use_summary_index else []
        candidates = self.merge_summary_sources(candidates, summaries, plan.queries)
        reranked, rerank_debug = rerank_chunks_with_debug(candidates, plan.queries, plan.rerank_top_k)
        reranked = balance_comparison_results(reranked, intent, plan.rerank_top_k)
        expanded = self.expand_neighbors(reranked, plan.neighbor_window)
        final = self.apply_context_budget(expanded, plan)

        debug_info.candidate_count = len(candidates)
        debug_info.summary_candidate_count = len(summaries)
        debug_info.reranked_count = len(reranked)
        debug_info.expanded_count = len(expanded)
        debug_info.final_count = len(final)
        debug_info.reranker_backend = "cross-encoder" if rerank_debug.used_cross_encoder else rerank_debug.backend
        if debug:
            debug_info.lines = build_debug_lines(intent, plan, candidates, reranked, final, summaries, debug_info.reranker_backend, rerank_debug.fallback_reason)
        return RetrievalResult(chunks=final, intent=intent, plan=plan, debug=debug_info)

    def collect_candidates(self, plan: RetrievalPlan) -> list[RetrievedChunk]:
        merged: dict[str, RetrievedChunk] = {}
        for planned_query in plan.queries:
            vector_results = self.vector.search(planned_query.query, plan.vector_initial_k, planned_query.metadata_filter)
            keyword_results = self.keyword.search(planned_query.query, plan.keyword_initial_k, planned_query.metadata_filter)
            for item in vector_results + keyword_results:
                merge_candidate(merged, item, planned_query)
        return list(merged.values())

    def collect_summary_records(self, plan: RetrievalPlan) -> list[SummaryRecord]:
        records: list[SummaryRecord] = []
        seen: set[str] = set()
        for query in plan.queries:
            for record in self.summary.search(query.query, 6, query.metadata_filter):
                if record.summary_id not in seen:
                    seen.add(record.summary_id)
                    records.append(record)
        return records

    def merge_summary_sources(self, candidates: list[RetrievedChunk], summaries: list[SummaryRecord], queries: list[PlannedQuery]) -> list[RetrievedChunk]:
        merged = {item.chunk.chunk_id: item for item in candidates}
        source_queries = [query.query for query in queries[:2]]
        for record in summaries:
            for index, chunk_id in enumerate(record.source_chunk_ids[:8]):
                chunk = self.chunk_by_id.get(chunk_id)
                if not chunk:
                    continue
                score = max(0.65 - index * 0.03, 0.2)
                existing = merged.get(chunk_id)
                if existing is None:
                    merged[chunk_id] = RetrievedChunk(chunk=chunk, score=score, rerank_score=score, matched_queries=source_queries, debug_reason=f"summary_source={record.summary_id}")
                else:
                    merged[chunk_id] = existing.model_copy(update={"rerank_score": max(existing.rerank_score or 0.0, score), "debug_reason": existing.debug_reason or f"summary_source={record.summary_id}"})
        return list(merged.values())

    def expand_neighbors(self, reranked: list[RetrievedChunk], neighbor_window: int) -> list[RetrievedChunk]:
        if neighbor_window <= 0:
            return reranked
        expanded: list[RetrievedChunk] = []
        seen: set[str] = set()
        for item in reranked:
            chunk = item.chunk
            seed_score = item.rerank_score or item.score or 0.0
            for offset in range(-neighbor_window, neighbor_window + 1):
                neighbor = self.position_index.get((chunk.book_id, chunk.chapter_index, chunk.chunk_index + offset))
                if not neighbor or neighbor.chunk_id in seen:
                    continue
                seen.add(neighbor.chunk_id)
                if offset == 0:
                    expanded.append(item)
                else:
                    score = max(seed_score - 0.08 * abs(offset), 0.0)
                    expanded.append(RetrievedChunk(chunk=neighbor, score=score, rerank_score=score, matched_queries=item.matched_queries, debug_reason=f"neighbor offset={offset}"))
        expanded.sort(key=lambda item: item.rerank_score or item.score or 0.0, reverse=True)
        return expanded

    def apply_context_budget(self, expanded: list[RetrievedChunk], plan: RetrievalPlan) -> list[RetrievedChunk]:
        selected: list[RetrievedChunk] = []
        total_chars = 0
        min_keep = min(5, len(expanded))
        max_keep = max(plan.final_top_k, min_keep)
        for item in expanded:
            next_total = total_chars + len(item.chunk.text)
            if selected and len(selected) >= min_keep and (next_total > plan.context_max_chars or len(selected) >= max_keep):
                break
            selected.append(item)
            total_chars = next_total
        return selected


def merge_candidate(merged: dict[str, RetrievedChunk], item: RetrievedChunk, query: PlannedQuery) -> None:
    chunk_id = item.chunk.chunk_id
    existing = merged.get(chunk_id)
    if existing is None:
        merged[chunk_id] = item.model_copy(update={"matched_queries": unique_strings(item.matched_queries + [query.query])})
        return
    merged[chunk_id] = existing.model_copy(update={
        "vector_score": best_vector_score(existing.vector_score, item.vector_score),
        "keyword_score": max(existing.keyword_score or 0.0, item.keyword_score or 0.0) or None,
        "matched_queries": unique_strings(existing.matched_queries + item.matched_queries + [query.query]),
    })


def best_vector_score(left: float | None, right: float | None) -> float | None:
    if left is None:
        return right
    if right is None:
        return left
    return min(left, right)


def balance_comparison_results(reranked: list[RetrievedChunk], intent: IntentAnalysis, top_k: int) -> list[RetrievedChunk]:
    if "comparison" not in intent.labels or len(intent.book_titles) < 2:
        return reranked
    quota = max(2, min(5, top_k // len(intent.book_titles)))
    selected: list[RetrievedChunk] = []
    seen: set[str] = set()
    # Reserve the first positions for one result from every target book.
    for title in intent.book_titles:
        for item in reranked:
            if title in item.chunk.title and item.chunk.chunk_id not in seen:
                selected.append(item)
                seen.add(item.chunk.chunk_id)
                break
    for title in intent.book_titles:
        count = sum(1 for item in selected if title in item.chunk.title)
        for item in reranked:
            if item.chunk.chunk_id not in seen:
                if title in item.chunk.title:
                    selected.append(item)
                    seen.add(item.chunk.chunk_id)
                    count += 1
            if count >= quota:
                break
    for item in reranked:
        if item.chunk.chunk_id not in seen:
            selected.append(item)
            seen.add(item.chunk.chunk_id)
        if len(selected) >= top_k:
            break
    return selected[:top_k]


def build_debug_lines(intent: IntentAnalysis, plan: RetrievalPlan, candidates: list[RetrievedChunk], reranked: list[RetrievedChunk], final: list[RetrievedChunk], summaries: list[SummaryRecord], backend: str, fallback_reason: str = "") -> list[str]:
    lines = [
        f"意图：{'、'.join(intent.labels) or '未识别'}；需求：{'、'.join(intent.need_types) or '未识别'}",
        f"检索：{len(plan.queries)} 个查询，初召回 {len(candidates)} 条，摘要命中 {len(summaries)} 条，重排 {len(reranked)} 条，最终 {len(final)} 条；reranker={backend}",
        f"查询改写：{'；'.join(query.query for query in plan.queries)}",
    ]
    if fallback_reason:
        lines.append(f"reranker 回退原因：{fallback_reason[:160]}")
    for index, item in enumerate(final, start=1):
        chapter = item.chunk.chapter_title or f"第 {item.chunk.chapter_index} 章"
        score = item.rerank_score if item.rerank_score is not None else item.score
        lines.append(f"[{index}] 相关度 {format_relevance(score)} | {item.chunk.title} | {item.chunk.author or '未知'} | {chapter} | 段落 {item.chunk.start_paragraph_index}-{item.chunk.end_paragraph_index}")
    return lines


def format_relevance(score: float | None) -> str:
    if score is None:
        return "未知"
    if score >= 0.75:
        return "高"
    if score >= 0.45:
        return "中"
    return "低"


def unique_strings(values: list[str]) -> list[str]:
    return list(dict.fromkeys(value for value in values if value))
