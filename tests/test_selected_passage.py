import asyncio
from types import SimpleNamespace

from app.agent import answer_generator
from app.models.schemas import EvidenceAssessment, IntentAnalysis, IterativeRetrievalResult, RetrievalPlan
from app.services.reading_assistant import ReadingAssistantService


def prepare_selection():
    service = ReadingAssistantService()
    session = service.create_session()
    retrieval = IterativeRetrievalResult(
        intent=IntentAnalysis(question="解释这段文字"),
        plan=RetrievalPlan(),
        assessment=EvidenceAssessment(sufficient=False),
    )
    service._iterative = SimpleNamespace(search=lambda *_args: retrieval)
    prepared = service.prepare_turn(
        session.session_id,
        "解释这段文字",
        scope="book",
        book_id="book",
        book_title="测试书",
        chapter_index=1,
        selected_text="风在新写下的诗行中停留。",
        selected_paragraphs=[4, 5],
    )
    return service, prepared


def assert_selection_in_prompt(prompt):
    assert "风在新写下的诗行中停留。" in prompt
    assert "测试书" in prompt
    assert "章节序号：2" in prompt
    assert "段落索引：4-5" in prompt
    assert "解释这段文字" in prompt


def test_generate_passes_selection_to_model_when_index_returns_no_chunks(monkeypatch):
    service, prepared = prepare_selection()
    prompts = []

    def invoke(prompt):
        prompts.append(prompt)
        return SimpleNamespace(content="这是针对选文的解释。[选文]")

    monkeypatch.setattr(answer_generator, "make_llm", lambda: SimpleNamespace(invoke=invoke))
    answer = service.generate(prepared)

    assert len(prompts) == 1
    assert_selection_in_prompt(prompts[0])
    assert answer.answer == "这是针对选文的解释。[选文]"
    assert answer.citations == []
    assert prepared.resolved.original_question == "解释这段文字"


def test_stream_passes_selection_to_model_when_index_returns_no_chunks(monkeypatch):
    _, prepared = prepare_selection()
    prompts = []
    closed = []

    class Client:
        async def aclose(self):
            closed.append(True)

    class Llm:
        def __init__(self, **_kwargs):
            pass

        async def astream(self, prompt):
            prompts.append(prompt)
            yield SimpleNamespace(content="选文解释")

    monkeypatch.setattr(answer_generator, "llm_kwargs", lambda: {})
    monkeypatch.setattr(answer_generator.httpx, "AsyncClient", lambda **_kwargs: Client())
    monkeypatch.setattr(answer_generator, "ChatOpenAI", Llm)

    async def collect():
        return [part async for part in answer_generator.stream_answer(
            prepared.resolved.original_question, [], selection=prepared.selection,
        )]

    assert asyncio.run(collect()) == ["选文解释"]
    assert_selection_in_prompt(prompts[0])
    assert closed == [True]
