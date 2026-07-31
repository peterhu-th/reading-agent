from app.config import Settings, get_settings
from app.models.schemas import IntentAnalysis, PlannedQuery, RetrievalPlan


SUMMARY_LABELS = {"summary", "comparison", "recommendation", "emotion", "scene"}
SUMMARY_INDEX_LABELS = {"summary", "comparison", "detail"}


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

    return RetrievalPlan(
        queries=build_planned_queries(intent),
        vector_initial_k=settings.VECTOR_INITIAL_K,
        keyword_initial_k=settings.KEYWORD_INITIAL_K,
        rerank_candidate_k=settings.RERANK_CANDIDATE_K,
        rerank_top_k=rerank_top_k,
        final_top_k=final_top_k,
        neighbor_window=neighbor_window,
        context_max_chars=settings.CONTEXT_MAX_CHARS,
        use_summary_index=bool(labels & SUMMARY_INDEX_LABELS),
    )


def build_planned_queries(intent: IntentAnalysis) -> list[PlannedQuery]:
    filters: dict[str, str | list[str]] = {}
    if intent.book_titles:
        filters["title"] = intent.book_titles
    if intent.authors:
        filters["author"] = intent.authors

    queries = [PlannedQuery(query=intent.question, metadata_filter=filters, purpose="original")]
    for requirement in intent.evidence_requirements:
        requirement_filter = dict(filters)
        if requirement.target_books:
            requirement_filter["title"] = requirement.target_books
        queries.append(
            PlannedQuery(
                query=requirement.query,
                metadata_filter=requirement_filter,
                purpose=requirement.purpose,
            )
        )

    base_terms = unique_strings(intent.topics + intent.emotions)
    if base_terms:
        queries.append(
            PlannedQuery(query=" ".join(base_terms), metadata_filter=filters, purpose="topics")
        )
    if "summary" in intent.labels:
        title_text = " ".join(intent.book_titles)
        queries.append(
            PlannedQuery(
                query=f"{title_text} 主要内容 情节 主题 人物关系".strip(),
                metadata_filter=filters,
                purpose="summary",
            )
        )
    if "comparison" in intent.labels:
        for title in intent.book_titles:
            comparison_terms = " ".join(base_terms) or intent.question
            queries.append(
                PlannedQuery(
                    query=f"{title} {comparison_terms} 主题 观念 差异".strip(),
                    metadata_filter={"title": title},
                    purpose="comparison_target",
                )
            )
    if "emotion" in intent.labels or "recommendation" in intent.labels:
        queries.append(
            PlannedQuery(
                query=f"{' '.join(intent.emotions)} {' '.join(intent.topics)} 片段 主题 处境".strip(),
                metadata_filter=filters,
                purpose="recommendation",
            )
        )
    return dedupe_queries(queries)[:8]


def dedupe_queries(queries: list[PlannedQuery]) -> list[PlannedQuery]:
    result: list[PlannedQuery] = []
    seen: set[tuple[str, str]] = set()
    for query in queries:
        normalized = query.query.strip()
        if not normalized:
            continue
        key = (normalized, str(sorted(query.metadata_filter.items())))
        if key not in seen:
            seen.add(key)
            result.append(query.model_copy(update={"query": normalized}))
    return result


def unique_strings(values: list[str]) -> list[str]:
    return list(dict.fromkeys(value for value in values if value))
