from fastapi.testclient import TestClient

from app.models.schemas import (
    EvidenceAssessment,
    IntentAnalysis,
    IterativeRetrievalResult,
    RetrievalPlan,
    RetrievedChunk,
    TextChunk,
)
from app.services.reading_assistant import PreparedTurn
from app.web import api as web_api


client = TestClient(web_api.app)


def make_chunk() -> RetrievedChunk:
    return RetrievedChunk(
        chunk=TextChunk(
            chunk_id="book:0:0",
            book_id="book",
            title="荒原狼",
            author="黑塞",
            chapter_index=0,
            chapter_title="第一章",
            chunk_index=0,
            start_paragraph_index=0,
            end_paragraph_index=1,
            text="这是用于接口测试的原文。",
        )
    )


def test_session_create_get_delete():
    created = client.post("/api/sessions")
    assert created.status_code == 201
    session_id = created.json()["session_id"]
    assert client.get(f"/api/sessions/{session_id}").status_code == 200
    assert client.delete(f"/api/sessions/{session_id}").status_code == 204
    assert client.get(f"/api/sessions/{session_id}").status_code == 404


def test_busy_session_returns_conflict():
    session_id = client.post("/api/sessions").json()["session_id"]
    web_api.service.store.acquire(session_id)
    try:
        response = client.post("/api/chat/stream", json={"session_id": session_id, "question": "问题"})
    finally:
        web_api.service.store.release(session_id)
    assert response.status_code == 409


def test_stream_emits_status_answer_and_citations(monkeypatch):
    session = web_api.service.create_session()
    retrieved = make_chunk()
    retrieval = IterativeRetrievalResult(
        chunks=[retrieved],
        intent=IntentAnalysis(question="问题"),
        plan=RetrievalPlan(),
        assessment=EvidenceAssessment(sufficient=True),
    )

    def fake_prepare(session_id, question, selected_books, debug, status_callback):
        current = web_api.service.store.get(session_id)
        status_callback("retrieving", 0, "正在检索原文")
        status_callback("checking", 0, "正在检查证据覆盖")
        from app.models.schemas import ResolvedQuestion
        return PreparedTurn(current, ResolvedQuestion(original_question=question, standalone_question=question), retrieval)

    async def fake_stream(*_args, **_kwargs):
        yield "测试回答[1]"

    monkeypatch.setattr(web_api.service, "prepare_turn", fake_prepare)
    monkeypatch.setattr(web_api, "stream_answer", fake_stream)
    monkeypatch.setattr(web_api.service, "finalize_turn", lambda prepared, answer: prepared.session)

    response = client.post("/api/chat/stream", json={"session_id": session.session_id, "question": "问题"})
    body = response.text

    assert response.status_code == 200
    assert body.index("understanding") < body.index("retrieving") < body.index("checking") < body.index("answering")
    assert "answer_delta" in body
    assert "citations" in body
    assert "book:0:0" not in body


def test_source_response_does_not_expose_internal_id(monkeypatch):
    monkeypatch.setattr(web_api.service, "get_source", lambda _source_id: make_chunk().chunk)
    response = client.get("/api/sources/opaque-source")
    assert response.status_code == 200
    assert "chunk_id" not in response.json()
    assert "source_path" not in response.json()
