from app.agent.answer_strategy import choose_answer_strategy
from app.agent.intent_analyzer import fallback_intent
from app.memory.conversation_memory import update_conversation
from app.models.schemas import (
    AnswerWithCitations,
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
from app.retrieval.hybrid_retriever import EnhancedRetriever
from app.retrieval.reranker import rerank_chunks_with_debug


def make_chunk(index: int, title: str = "荒原狼") -> TextChunk:
    return TextChunk(
        chunk_id=f"book:0:{index}",
        book_id="book",
        title=title,
        author="黑塞",
        chapter_index=0,
        chapter_title="第一章",
        chunk_index=index,
        start_paragraph_index=index,
        end_paragraph_index=index,
        text=f"哈里 感到 孤独 与 分裂 {index}",
    )


def test_fallback_intent_uses_conversation_for_follow_up():
    session = ConversationSession(active_book_titles=["荒原狼"], active_topics=["孤独"])

    intent = fallback_intent("那他后来怎么样了？", session)

    assert intent.book_titles == ["荒原狼"]
    assert "孤独" in intent.topics


def test_answer_strategy_summary_is_longer():
    intent = IntentAnalysis(
        labels=["summary"],
        need_types=["summarize"],
        question="《荒原狼》主要讲了什么？",
    )

    strategy = choose_answer_strategy(intent)

    assert strategy.name == "summarize"
    assert strategy.min_chars >= 800


def test_conversation_update_keeps_active_title():
    retrieval = RetrievalResult(
        chunks=[RetrievedChunk(chunk=make_chunk(0))],
        intent=IntentAnalysis(
            labels=["specified_book"],
            need_types=["explain"],
            book_titles=["荒原狼"],
            topics=["孤独"],
            question="《荒原狼》如何写孤独？",
        ),
        plan=RetrievalPlan(queries=[PlannedQuery(query="孤独")]),
        debug=RetrievalDebugInfo(),
    )

    updated = update_conversation(
        ConversationSession(),
        "《荒原狼》如何写孤独？",
        retrieval,
        AnswerWithCitations(answer="它通过哈里的处境写孤独。[1]"),
    )

    assert updated.active_book_titles == ["荒原狼"]
    assert updated.active_topics == ["孤独"]


def test_summary_sources_merge_back_to_original_chunks(monkeypatch):
    retriever = EnhancedRetriever.__new__(EnhancedRetriever)
    chunk = make_chunk(0)
    monkeypatch.setattr(EnhancedRetriever, "chunk_by_id", {chunk.chunk_id: chunk})
    summary = SummaryRecord(
        summary_id="book:chapter:0",
        summary_type="chapter",
        book_id="book",
        title="荒原狼",
        author="黑塞",
        chapter_index=0,
        chapter_title="第一章",
        source_chunk_ids=[chunk.chunk_id],
        source_hash="hash",
        summary_model="test",
        prompt_version="v1",
        text="章节摘要",
    )

    merged = EnhancedRetriever.merge_summary_sources(
        retriever,
        [],
        [summary],
        [PlannedQuery(query="荒原狼 主要内容")],
    )

    assert len(merged) == 1
    assert merged[0].chunk.chunk_id == chunk.chunk_id


def test_reranker_returns_lightweight_debug(monkeypatch):
    from app.config import get_settings

    monkeypatch.setenv("OPENAI_API_KEY", "test")
    monkeypatch.setenv("OPENAI_BASE_URL", "http://127.0.0.1:3000/v1")
    monkeypatch.setenv("RERANK_BACKEND", "lightweight")
    get_settings.cache_clear()

    ranked, debug = rerank_chunks_with_debug(
        [RetrievedChunk(chunk=make_chunk(0), keyword_score=1.0)],
        [PlannedQuery(query="孤独")],
        top_k=1,
    )

    assert ranked
    assert debug.backend == "lightweight"
    get_settings.cache_clear()
