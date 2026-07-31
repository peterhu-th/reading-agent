from app.memory.conversation_resolver import fallback_resolve, resolve_question
from app.models.schemas import (
    ConversationSession,
    ConversationTurn,
    EvidenceAssessment,
    EvidenceRequirement,
    IntentAnalysis,
    PlannedQuery,
    RetrievalDebugInfo,
    RetrievalPlan,
    RetrievalResult,
    RetrievedChunk,
    TextChunk,
)
from app.retrieval.evidence_checker import rule_assessment
from app.retrieval.iterative_retriever import IterativeRetriever


def make_chunk(index: int, title: str = "荒原狼") -> RetrievedChunk:
    return RetrievedChunk(
        chunk=TextChunk(
            chunk_id=f"book:0:{index}",
            book_id="book",
            title=title,
            author="黑塞",
            chapter_index=0,
            chapter_title="第一章",
            chunk_index=index,
            start_paragraph_index=index,
            end_paragraph_index=index,
            text=f"哈勒后来重新理解孤独和自我分裂。证据 {index}",
        ),
        rerank_score=1.0 - index * 0.1,
    )


def make_result(chunks: list[RetrievedChunk]) -> RetrievalResult:
    intent = IntentAnalysis(
        question="荒原狼中的孤独",
        labels=["detail"],
        book_titles=["荒原狼"],
        evidence_requirements=[EvidenceRequirement(description="找到孤独的原文", query="荒原狼 孤独", target_books=["荒原狼"])],
    )
    return RetrievalResult(
        chunks=chunks,
        intent=intent,
        plan=RetrievalPlan(queries=[PlannedQuery(query="荒原狼 孤独")]),
        debug=RetrievalDebugInfo(candidate_count=len(chunks)),
    )


class FakeEnhancedRetriever:
    def __init__(self) -> None:
        self.supplement_calls = 0

    def search(self, *_args, **_kwargs) -> RetrievalResult:
        return make_result([make_chunk(0)])

    def search_with_plan(self, intent, plan, debug=False) -> RetrievalResult:
        self.supplement_calls += 1
        result = make_result([make_chunk(self.supplement_calls)])
        return result.model_copy(update={"intent": intent, "plan": plan})

    def expand_neighbors(self, chunks, _window):
        return chunks

    def apply_context_budget(self, chunks, _plan):
        return chunks


def test_iterative_retrieval_stops_after_two_supplement_rounds(monkeypatch):
    fake = FakeEnhancedRetriever()
    retriever = IterativeRetriever(fake)  # type: ignore[arg-type]
    retriever.settings = retriever.settings.model_copy(update={"RETRIEVAL_MAX_SUPPLEMENT_ROUNDS": 2})

    def always_missing(*_args, **_kwargs):
        return EvidenceAssessment(
            sufficient=False,
            missing_aspects=["仍缺证据"],
            supplemental_queries=[PlannedQuery(query="补充证据")],
        )

    monkeypatch.setattr("app.retrieval.iterative_retriever.assess_evidence", always_missing)
    result = retriever.search("问题")

    assert fake.supplement_calls == 2
    assert len(result.rounds) == 3
    assert result.rounds[-1].stop_reason == "max_rounds"


def test_rule_checker_requires_each_comparison_book(monkeypatch):
    from app.config import get_settings

    monkeypatch.setenv("OPENAI_API_KEY", "test")
    monkeypatch.setenv("OPENAI_BASE_URL", "http://127.0.0.1:3000/v1")
    get_settings.cache_clear()
    intent = IntentAnalysis(
        question="比较两本书的孤独",
        labels=["comparison"],
        book_titles=["荒原狼", "瓦尔登湖"],
        topics=["孤独"],
        evidence_requirements=[EvidenceRequirement(description="比较孤独", query="孤独")],
    )

    assessment = rule_assessment(intent, [make_chunk(i) for i in range(5)])

    assert not assessment.sufficient
    assert any("瓦尔登湖" in item for item in assessment.missing_aspects)
    assert any(query.metadata_filter.get("title") == "瓦尔登湖" for query in assessment.supplemental_queries)
    get_settings.cache_clear()


def test_selected_book_overrides_conversation_context():
    session = ConversationSession(
        active_book_titles=["荒原狼"],
        turns=[ConversationTurn(user_question="荒原狼讲了什么")],
    )

    resolved = resolve_question("那后来怎么样了？", session, ["红楼梦"])

    assert resolved.inherited_book_titles == ["红楼梦"]
    assert "《红楼梦》" in resolved.standalone_question


def test_ambiguous_follow_up_requests_clarification():
    session = ConversationSession(
        active_book_titles=["荒原狼", "瓦尔登湖"],
        turns=[ConversationTurn(user_question="比较两本书")],
    )

    resolved = fallback_resolve("那它后来怎么样？", session)

    assert resolved.clarification_needed
    assert resolved.confidence < 0.45
