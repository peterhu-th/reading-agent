from app.config import get_settings
from app.models.schemas import PlannedQuery, RetrievedChunk, RerankDebugInfo
from app.retrieval.cross_encoder_reranker import CrossEncoderReranker
from app.retrieval.keyword_retriever import metadata_matches, tokenize


def rerank_chunks(
    candidates: list[RetrievedChunk],
    planned_queries: list[PlannedQuery],
    top_k: int,
) -> list[RetrievedChunk]:
    ranked, _ = rerank_chunks_with_debug(candidates, planned_queries, top_k)
    return ranked


def rerank_chunks_with_debug(
    candidates: list[RetrievedChunk],
    planned_queries: list[PlannedQuery],
    top_k: int,
) -> tuple[list[RetrievedChunk], RerankDebugInfo]:
    if not candidates:
        return [], RerankDebugInfo()

    settings = get_settings()
    lightweight = lightweight_rerank(candidates, planned_queries)
    limited = lightweight[: settings.RERANK_CANDIDATE_K]
    if settings.RERANK_BACKEND.strip().lower() in {"cross_encoder", "cross-encoder"}:
        reranked, debug = CrossEncoderReranker().rerank(limited, planned_queries)
        if debug.used_cross_encoder:
            return reranked[:top_k], debug
        return lightweight[:top_k], debug

    return lightweight[:top_k], RerankDebugInfo(backend="lightweight")


def lightweight_rerank(
    candidates: list[RetrievedChunk],
    planned_queries: list[PlannedQuery],
) -> list[RetrievedChunk]:
    vector_values = [item.vector_score for item in candidates if item.vector_score is not None]
    keyword_values = [item.keyword_score for item in candidates if item.keyword_score is not None]
    max_keyword = max(keyword_values) if keyword_values else 0.0
    max_vector = max(vector_values) if vector_values else 0.0
    min_vector = min(vector_values) if vector_values else 0.0

    query_tokens = set()
    for planned in planned_queries:
        query_tokens.update(tokenize(planned.query))

    reranked: list[RetrievedChunk] = []
    for item in candidates:
        chunk = item.chunk
        vector_component = normalize_vector_score(item.vector_score, min_vector, max_vector)
        keyword_component = (item.keyword_score or 0.0) / max_keyword if max_keyword > 0 else 0.0
        metadata_component = metadata_score(item, planned_queries)
        overlap_component = token_overlap_score(query_tokens, tokenize(chunk.text))
        title_component = token_overlap_score(query_tokens, tokenize(chunk.title))

        score = (
            0.35 * vector_component
            + 0.25 * keyword_component
            + 0.2 * metadata_component
            + 0.15 * overlap_component
            + 0.05 * title_component
        )
        reason = (
            f"v={vector_component:.3f} bm25={keyword_component:.3f} "
            f"meta={metadata_component:.3f} overlap={overlap_component:.3f}"
        )
        reranked.append(
            item.model_copy(
                update={
                    "score": score,
                    "rerank_score": score,
                    "debug_reason": reason,
                }
            )
        )

    reranked.sort(key=lambda item: item.rerank_score or 0.0, reverse=True)
    return reranked


def normalize_vector_score(value: float | None, min_value: float, max_value: float) -> float:
    """Convert Chroma distance-like scores into higher-is-better relevance."""
    if value is None:
        return 0.0
    if max_value == min_value:
        return 1.0
    return 1.0 - ((value - min_value) / (max_value - min_value))


def metadata_score(item: RetrievedChunk, planned_queries: list[PlannedQuery]) -> float:
    filters = [query.metadata_filter for query in planned_queries if query.metadata_filter]
    if not filters:
        return 0.0
    matches = sum(1 for metadata_filter in filters if metadata_matches(item.chunk, metadata_filter))
    return matches / len(filters)


def token_overlap_score(query_tokens: set[str], text_tokens: list[str]) -> float:
    if not query_tokens or not text_tokens:
        return 0.0
    text_set = set(text_tokens)
    return len(query_tokens & text_set) / len(query_tokens)
