import json

from fastapi.testclient import TestClient

from app.models.schemas import (
    EvidenceAssessment,
    IntentAnalysis,
    IterativeRetrievalResult,
    RetrievalPlan,
    ReaderPage,
    ReaderBook,
    ReaderChapter,
    RetrievedChunk,
    ResolvedQuestion,
    SelectedPassage,
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

    def fake_prepare(session_id, question, selected_books, debug, status_callback, *_scope_args):
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


def test_reader_response_does_not_expose_local_path(monkeypatch):
    page = ReaderPage(
        book=ReaderBook(book_id="book", title="荒原狼", author="黑塞"),
        chapter=ReaderChapter(book_id="book", chapter_index=0, chapter_title="第一章"),
        paragraphs=[],
    )
    monkeypatch.setattr(web_api.reader, "get_chapter", lambda *_args: page)
    response = client.get("/api/reader/books/book/chapters/0")
    assert response.status_code == 200
    assert "source_path" not in response.json()


def test_delete_chapter_endpoint(monkeypatch):
    monkeypatch.setattr(web_api.editor, "delete_chapter", lambda *_args: web_api.editor.state())
    response = client.delete("/api/editor/books/book/chapters/3")
    assert response.status_code == 200
    assert "undo_count" in response.json()
    action_response = client.post("/api/editor/books/book/chapters/3/delete")
    assert action_response.status_code == 200


def test_rename_chapter_endpoint(monkeypatch):
    monkeypatch.setattr(web_api.editor, "rename_chapter", lambda *_args: web_api.editor.state())
    response = client.post("/api/editor/books/book/chapters/3/rename", json={"title": "新章节名称"})
    assert response.status_code == 200
    assert "undo_count" in response.json()


def test_health_advertises_editor_capabilities():
    response = client.get("/api/health")
    assert response.status_code == 200
    assert response.json()["web_api_version"] >= 4
    assert "chapter_delete" in response.json()["editor_capabilities"]
    assert "chapter_rename" in response.json()["editor_capabilities"]


def test_safe_error_message_hides_internal_path():
    message = web_api.safe_error_message(RuntimeError("failed at D:/private/models/config.json"))
    assert "D:/" not in message
    assert "config.json" not in message


def test_stream_forwards_selected_passage_to_answer_generation(monkeypatch):
    session = web_api.service.create_session()
    selection = SelectedPassage(text="没有进入索引的选文", book_title="测试书", chapter_index=0, paragraph_indices=[2])
    prepared = PreparedTurn(
        session,
        ResolvedQuestion(original_question="解释选文", standalone_question="解释选文"),
        IterativeRetrievalResult(intent=IntentAnalysis(question="解释选文"), plan=RetrievalPlan(), assessment=EvidenceAssessment()),
        selection=selection,
    )
    received = []

    async def fake_stream(*_args, **kwargs):
        received.append(kwargs.get("selection"))
        yield "选文解释"

    monkeypatch.setattr(web_api.service, "prepare_turn", lambda *_args: prepared)
    monkeypatch.setattr(web_api.service, "finalize_turn", lambda *_args: session)
    monkeypatch.setattr(web_api, "stream_answer", fake_stream)

    response = client.post("/api/chat/stream", json={"session_id": session.session_id, "question": "解释选文", "selected_text": selection.text})

    assert response.status_code == 200
    assert "选文解释" in response.text
    assert received == [selection]


def test_citations_keep_source_coordinates_when_text_occurs_in_multiple_chapters(monkeypatch):
    session = web_api.service.create_session()
    retrieved = make_chunk()
    retrieved.chunk.chapter_index = 2
    retrieved.chunk.start_paragraph_index = 5
    retrieved.chunk.end_paragraph_index = 8
    prepared = PreparedTurn(
        session,
        ResolvedQuestion(original_question="问题", standalone_question="问题"),
        IterativeRetrievalResult(chunks=[retrieved], intent=IntentAnalysis(question="问题"), plan=RetrievalPlan(), assessment=EvidenceAssessment(sufficient=True)),
    )

    async def fake_stream(*_args, **_kwargs):
        yield "回答[1]"

    def ambiguous_text_lookup(*_args):
        raise AssertionError("Precise source coordinates must not be replaced by a first-text-match lookup")

    monkeypatch.setattr(web_api.service, "prepare_turn", lambda *_args: prepared)
    monkeypatch.setattr(web_api.service, "finalize_turn", lambda *_args: session)
    monkeypatch.setattr(web_api, "stream_answer", fake_stream)
    monkeypatch.setattr(web_api.reader, "locate", ambiguous_text_lookup)
    response = client.post("/api/chat/stream", json={"session_id": session.session_id, "question": "问题"})

    events = response.text.split("\n\n")
    payload = next(event.split("data: ", 1)[1] for event in events if event.startswith("event: citations"))
    location = json.loads(payload)["items"][0]["reader_location"]
    assert location == {"book_id": "book", "chapter_index": 2, "start_paragraph_index": 5, "end_paragraph_index": 8}
