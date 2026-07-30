from app.ingestion.chunking import chunk_paragraphs
from app.models.schemas import BookParagraph, IntentAnalysis, RetrievedChunk, TextChunk
from app.retrieval.hybrid_retriever import balance_comparison_results
from app.retrieval.query_planner import plan_retrieval


def make_paragraph(index: int, title: str, text: str) -> BookParagraph:
    return BookParagraph(
        book_id=title,
        title=title,
        author="作者",
        source_path="test.epub",
        chapter_index=0,
        chapter_title="第一章",
        paragraph_index=index,
        text=text,
    )


def make_chunk(chunk_id: str, title: str, score: float) -> RetrievedChunk:
    chunk = TextChunk(
        chunk_id=chunk_id,
        book_id=title,
        title=title,
        author="作者",
        chapter_index=0,
        chapter_title="第一章",
        chunk_index=0,
        start_paragraph_index=0,
        end_paragraph_index=1,
        text=f"{title} 的证据片段。",
    )
    return RetrievedChunk(chunk=chunk, rerank_score=score)


def test_book_type_specific_chunking_uses_poetry_policy():
    paragraphs = [make_paragraph(i, "诗经", f"诗句第{i}行。") for i in range(20)]

    chunks = chunk_paragraphs(paragraphs)

    assert chunks
    assert {chunk.book_type for chunk in chunks} == {"poetry"}
    assert max(len(chunk.text) for chunk in chunks) <= 420


def test_book_type_specific_chunking_uses_philosophy_policy():
    paragraphs = [
        make_paragraph(i, "理想国", "正义、城邦、灵魂秩序的讨论。" * 12)
        for i in range(8)
    ]

    chunks = chunk_paragraphs(paragraphs)

    assert chunks
    assert {chunk.book_type for chunk in chunks} == {"philosophy"}
    assert max(len(chunk.text) for chunk in chunks) <= 760


def test_query_planner_adds_per_book_queries_for_comparison():
    intent = IntentAnalysis(
        question="比较《瓦尔登湖》和《荒原狼》对孤独的理解",
        labels=["comparison"],
        book_titles=["瓦尔登湖", "荒原狼"],
        topics=["孤独"],
    )

    plan = plan_retrieval(intent)
    comparison_queries = [query for query in plan.queries if query.purpose == "comparison_target"]

    assert len(comparison_queries) == 2
    assert {query.metadata_filter["title"] for query in comparison_queries} == {"瓦尔登湖", "荒原狼"}
    assert plan.final_top_k >= 14


def test_balance_comparison_results_keeps_each_target_book():
    ranked = [
        make_chunk("walden:0:0", "瓦尔登湖", 0.9),
        make_chunk("walden:0:1", "瓦尔登湖", 0.8),
        make_chunk("wolf:0:0", "荒原狼", 0.7),
    ]
    intent = IntentAnalysis(
        question="比较《瓦尔登湖》和《荒原狼》",
        labels=["comparison"],
        book_titles=["瓦尔登湖", "荒原狼"],
    )

    balanced = balance_comparison_results(ranked, intent, top_k=3)

    assert {item.chunk.title for item in balanced[:2]} == {"瓦尔登湖", "荒原狼"}
