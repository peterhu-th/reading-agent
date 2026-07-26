import json
from functools import cached_property
from pathlib import Path

from app.agent.intent_analyzer import analyze_intent
from app.config import get_settings
from app.models.schemas import (
    IntentAnalysis,
    PlannedQuery,
    RetrievalDebugInfo,
    RetrievalPlan,
    RetrievalResult,
    RetrievedChunk,
    TextChunk,
)
from app.retrieval.keyword_retriever import KeywordRetriever
from app.retrieval.query_planner import plan_retrieval
from app.retrieval.reranker import rerank_chunks
from app.retrieval.vector_retriever import VectorRetriever


class EnhancedRetriever:
    """Second-stage retriever with intent routing and hybrid retrieval."""

    def __init__(self) -> None:
        self.settings = get_settings()
        self.vector = VectorRetriever()
        self.keyword = KeywordRetriever()

    @cached_property
    def chunks(self) -> list[TextChunk]:
        path = Path(self.settings.CHUNKS_JSONL_PATH)
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
    def position_index(self) -> dict[tuple[str, int, int], TextChunk]:
        return {
            (chunk.book_id, chunk.chapter_index, chunk.chunk_index): chunk
            for chunk in self.chunks
        }

    def search(self, question: str, debug: bool = False) -> RetrievalResult:
        intent = analyze_intent(question)
        plan = plan_retrieval(intent, self.settings)
        debug_info = RetrievalDebugInfo()

        candidates = self.collect_candidates(plan, debug_info)
        reranked = rerank_chunks(candidates, plan.queries, plan.rerank_top_k)
        expanded = self.expand_neighbors(reranked, plan.neighbor_window)
        final = self.apply_context_budget(expanded, plan)

        debug_info.candidate_count = len(candidates)
        debug_info.reranked_count = len(reranked)
        debug_info.expanded_count = len(expanded)
        debug_info.final_count = len(final)
        if debug:
            debug_info.lines.extend(build_debug_lines(intent, plan, candidates, reranked, final))

        return RetrievalResult(chunks=final, intent=intent, plan=plan, debug=debug_info)

    def collect_candidates(
        self,
        plan: RetrievalPlan,
        debug_info: RetrievalDebugInfo,
    ) -> list[RetrievedChunk]:
        merged: dict[str, RetrievedChunk] = {}

        for planned_query in plan.queries:
            vector_results = self.vector.search(
                planned_query.query,
                top_k=plan.vector_initial_k,
                metadata_filter=planned_query.metadata_filter,
            )
            keyword_results = self.keyword.search(
                planned_query.query,
                top_k=plan.keyword_initial_k,
                metadata_filter=planned_query.metadata_filter,
            )

            for item in vector_results + keyword_results:
                merge_candidate(merged, item, planned_query)

        return list(merged.values())

    def expand_neighbors(
        self,
        reranked: list[RetrievedChunk],
        neighbor_window: int,
    ) -> list[RetrievedChunk]:
        if neighbor_window <= 0:
            return reranked

        expanded: list[RetrievedChunk] = []
        seen: set[str] = set()
        seed_scores = {item.chunk.chunk_id: item.rerank_score or item.score or 0.0 for item in reranked}

        for item in reranked:
            chunk = item.chunk
            for offset in range(-neighbor_window, neighbor_window + 1):
                key = (chunk.book_id, chunk.chapter_index, chunk.chunk_index + offset)
                neighbor = self.position_index.get(key)
                if not neighbor or neighbor.chunk_id in seen:
                    continue
                seen.add(neighbor.chunk_id)
                if offset == 0:
                    expanded.append(item)
                    continue

                distance_penalty = 0.08 * abs(offset)
                score = max((seed_scores[chunk.chunk_id] - distance_penalty), 0.0)
                expanded.append(
                    RetrievedChunk(
                        chunk=neighbor,
                        score=score,
                        rerank_score=score,
                        matched_queries=item.matched_queries,
                        debug_reason=f"neighbor offset={offset} from {chunk.chunk_id}",
                    )
                )

        expanded.sort(key=lambda item: item.rerank_score or item.score or 0.0, reverse=True)
        return expanded

    def apply_context_budget(
        self,
        expanded: list[RetrievedChunk],
        plan: RetrievalPlan,
    ) -> list[RetrievedChunk]:
        selected: list[RetrievedChunk] = []
        total_chars = 0
        min_keep = min(5, len(expanded))
        max_keep = max(plan.final_top_k, min_keep)

        for item in expanded:
            next_total = total_chars + len(item.chunk.text)
            if (
                selected
                and len(selected) >= min_keep
                and (next_total > plan.context_max_chars or len(selected) >= max_keep)
            ):
                break
            selected.append(item)
            total_chars = next_total

        return selected


def merge_candidate(
    merged: dict[str, RetrievedChunk],
    item: RetrievedChunk,
    planned_query: PlannedQuery,
) -> None:
    chunk_id = item.chunk.chunk_id
    existing = merged.get(chunk_id)
    if existing is None:
        merged[chunk_id] = item.model_copy(
            update={"matched_queries": unique_strings(item.matched_queries + [planned_query.query])}
        )
        return

    vector_score = best_vector_score(existing.vector_score, item.vector_score)
    keyword_score = max(existing.keyword_score or 0.0, item.keyword_score or 0.0) or None
    matched_queries = unique_strings(existing.matched_queries + item.matched_queries + [planned_query.query])
    merged[chunk_id] = existing.model_copy(
        update={
            "vector_score": vector_score,
            "keyword_score": keyword_score,
            "matched_queries": matched_queries,
        }
    )


def best_vector_score(left: float | None, right: float | None) -> float | None:
    if left is None:
        return right
    if right is None:
        return left
    return min(left, right)


def unique_strings(values: list[str]) -> list[str]:
    result: list[str] = []
    for value in values:
        if value and value not in result:
            result.append(value)
    return result


def build_debug_lines(
    intent: IntentAnalysis,
    plan: RetrievalPlan,
    candidates: list[RetrievedChunk],
    reranked: list[RetrievedChunk],
    final: list[RetrievedChunk],
) -> list[str]:
    title_filter = ", ".join(intent.book_titles) if intent.book_titles else "未限定"
    author_filter = ", ".join(intent.authors) if intent.authors else "未限定"
    lines = [
        f"意图：{format_list(intent.labels)}；需求：{format_list(intent.need_types)}",
        f"限定范围：书名={title_filter}；作者={author_filter}",
        f"检索策略：{len(plan.queries)} 个查询，初召回 {len(candidates)} 条，重排 {len(reranked)} 条，最终采用 {len(final)} 条证据；相邻扩展 ±{plan.neighbor_window}",
    ]
    query_text = "；".join(query.query for query in plan.queries)
    if query_text:
        lines.append(f"查询改写：{query_text}")
    for index, item in enumerate(final, start=1):
        score = item.rerank_score if item.rerank_score is not None else item.score
        score_text = format_relevance(score)
        lines.append(
            f"[{index}] 相关度={score_text}｜{item.chunk.title}｜"
            f"{item.chunk.author or 'unknown'}｜{item.chunk.chapter_title or f'第 {item.chunk.chapter_index} 章'}｜"
            f"段落 {item.chunk.start_paragraph_index}-{item.chunk.end_paragraph_index}"
        )
    return lines


def format_list(values: list[str]) -> str:
    return "、".join(values) if values else "未识别"


def format_relevance(score: float | None) -> str:
    if score is None:
        return "未知"
    if score >= 0.75:
        return "高"
    if score >= 0.45:
        return "中"
    return "低"
