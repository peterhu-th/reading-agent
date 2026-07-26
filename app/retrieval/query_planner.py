from app.config import Settings, get_settings
from app.models.schemas import IntentAnalysis, PlannedQuery, RetrievalPlan


SUMMARY_LABELS = {"summary", "comparison", "recommendation", "emotion", "scene"}


def plan_retrieval(intent: IntentAnalysis, settings: Settings | None = None) -> RetrievalPlan:
    settings = settings or get_settings()
    labels = set(intent.labels)

    neighbor_window = 2 if labels & SUMMARY_LABELS else 1
    final_top_k = settings.FINAL_TOP_K
    rerank_top_k = settings.RERANK_TOP_K
    if labels & {"summary", "comparison"}:
        final_top_k = max(final_top_k, 14)
        rerank_top_k = max(rerank_top_k, 20)
    elif labels & {"emotion", "recommendation"}:
        final_top_k = max(final_top_k, 12)

    queries = build_planned_queries(intent)
    return RetrievalPlan(
        queries=queries,
        vector_initial_k=settings.VECTOR_INITIAL_K,
        keyword_initial_k=settings.KEYWORD_INITIAL_K,
        rerank_top_k=rerank_top_k,
        final_top_k=final_top_k,
        neighbor_window=neighbor_window,
        context_max_chars=settings.CONTEXT_MAX_CHARS,
    )


def build_planned_queries(intent: IntentAnalysis) -> list[PlannedQuery]:
    filters: dict[str, str | list[str]] = {}
    if intent.book_titles:
        filters["title"] = intent.book_titles
    if intent.authors:
        filters["author"] = intent.authors

    base_terms = intent.topics + intent.emotions
    queries: list[PlannedQuery] = [
        PlannedQuery(query=intent.question, metadata_filter=filters, purpose="original")
    ]

    if base_terms:
        queries.append(
            PlannedQuery(
                query=" ".join(base_terms),
                metadata_filter=filters,
                purpose="topics",
            )
        )

    if "summary" in intent.labels:
        queries.append(
            PlannedQuery(
                query=f"{' '.join(intent.book_titles)} 主要内容 情节 主题".strip(),
                metadata_filter=filters,
                purpose="summary",
            )
        )

    if "comparison" in intent.labels:
        for title in intent.book_titles:
            queries.append(
                PlannedQuery(
                    query=f"{title} {' '.join(base_terms)}".strip(),
                    metadata_filter={"title": title},
                    purpose="comparison_target",
                )
            )

    if "emotion" in intent.labels or "recommendation" in intent.labels:
        queries.append(
            PlannedQuery(
                query=f"{' '.join(intent.emotions)} {' '.join(intent.topics)} 片段 主题".strip(),
                metadata_filter=filters,
                purpose="recommendation",
            )
        )

    deduped: list[PlannedQuery] = []
    seen: set[tuple[str, str]] = set()
    for query in queries:
        normalized = query.query.strip()
        if not normalized:
            continue
        key = (normalized, str(sorted(query.metadata_filter.items())))
        if key in seen:
            continue
        seen.add(key)
        deduped.append(query.model_copy(update={"query": normalized}))

    return deduped[:5]
