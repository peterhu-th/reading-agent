from app.models.schemas import PlannedQuery, RetrievalPlan, RetrievedChunk, TextChunk
from app.retrieval.hybrid_retriever import EnhancedRetriever, merge_candidate
from app.retrieval.keyword_retriever import metadata_matches, tokenize
from app.retrieval.reranker import rerank_chunks


def make_chunk(index: int, text: str = "孤独 荒原狼 text") -> TextChunk:
    return TextChunk(
        chunk_id=f"book:0:{index}",
        book_id="book",
        title="荒原狼",
        author="黑塞",
        chapter_index=0,
        chapter_title="",
        chunk_index=index,
        start_paragraph_index=index,
        end_paragraph_index=index,
        text=text,
    )


def test_tokenize_handles_chinese_and_english():
    tokens = tokenize("荒原狼 loneliness 123")

    assert "荒" in tokens
    assert "荒原" in tokens
    assert "loneliness" in tokens
    assert "123" in tokens


def test_metadata_matches_uses_contains():
    assert metadata_matches(make_chunk(0), {"title": ["荒原"]})
    assert not metadata_matches(make_chunk(0), {"title": ["红楼梦"]})


def test_merge_candidate_preserves_best_scores():
    merged = {}
    query = PlannedQuery(query="孤独")
    chunk = make_chunk(0)

    merge_candidate(
        merged,
        RetrievedChunk(chunk=chunk, vector_score=0.5, matched_queries=["q1"]),
        query,
    )
    merge_candidate(
        merged,
        RetrievedChunk(chunk=chunk, vector_score=0.2, keyword_score=3.0, matched_queries=["q2"]),
        query,
    )

    item = merged[chunk.chunk_id]
    assert item.vector_score == 0.2
    assert item.keyword_score == 3.0
    assert item.matched_queries == ["q1", "孤独", "q2"]


def test_rerank_prefers_metadata_and_keyword_overlap():
    query = PlannedQuery(query="荒原狼 孤独", metadata_filter={"title": "荒原狼"})
    candidates = [
        RetrievedChunk(chunk=make_chunk(0), vector_score=0.3, keyword_score=2.0),
        RetrievedChunk(chunk=make_chunk(1, "unrelated"), vector_score=0.1, keyword_score=0.1),
    ]

    ranked = rerank_chunks(candidates, [query], top_k=2)

    assert ranked[0].chunk.chunk_id == "book:0:0"
    assert ranked[0].rerank_score is not None


def test_expand_neighbors_stays_in_same_book_and_chapter(monkeypatch):
    retriever = EnhancedRetriever.__new__(EnhancedRetriever)
    chunks = [make_chunk(0), make_chunk(1), make_chunk(2)]
    monkeypatch.setattr(EnhancedRetriever, "position_index", {(c.book_id, c.chapter_index, c.chunk_index): c for c in chunks})

    expanded = EnhancedRetriever.expand_neighbors(
        retriever,
        [RetrievedChunk(chunk=chunks[1], rerank_score=1.0)],
        neighbor_window=1,
    )

    assert [item.chunk.chunk_index for item in expanded] == [1, 0, 2]


def test_context_budget_keeps_at_least_five():
    retriever = EnhancedRetriever.__new__(EnhancedRetriever)
    chunks = [
        RetrievedChunk(chunk=make_chunk(i, "x" * 1000), rerank_score=1.0 - i / 10)
        for i in range(6)
    ]
    plan = RetrievalPlan(
        queries=[PlannedQuery(query="x")],
        final_top_k=10,
        context_max_chars=1200,
    )

    selected = EnhancedRetriever.apply_context_budget(retriever, chunks, plan)

    assert len(selected) == 5
