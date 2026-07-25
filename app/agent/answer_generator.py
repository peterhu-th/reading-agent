from pathlib import Path

import httpx
from langchain_openai import ChatOpenAI

from app.agent.citation_builder import build_citations, build_context
from app.config import get_settings
from app.models.schemas import AnswerWithCitations, RetrievedChunk


PROMPT_PATH = Path("app/prompts/answer_with_citations.md")


def load_prompt() -> str:
    return PROMPT_PATH.read_text(encoding="utf-8")


def generate_answer(question: str, retrieved: list[RetrievedChunk]) -> AnswerWithCitations:
    if not retrieved:
        return AnswerWithCitations(answer="当前书库证据不足。", citations=[])

    settings = get_settings()
    context = build_context(retrieved)
    citations = build_citations(retrieved)
    prompt = load_prompt().format(question=question, context=context)

    llm = ChatOpenAI(
        model=settings.CHAT_MODEL,
        api_key=settings.OPENAI_API_KEY,
        base_url=settings.OPENAI_BASE_URL,
        http_client=httpx.Client(trust_env=False),
        http_socket_options=(),
    )
    try:
        response = llm.invoke(prompt)
    except Exception as exc:
        if "token_invalidated" in str(exc) or "401 Unauthorized" in str(exc):
            raise RuntimeError(
                "AIClient2API ChatGPT/Codex OAuth token is invalidated. "
                "Please sign in again in AIClient2API, then rerun RAG."
            ) from exc
        raise
    return AnswerWithCitations(answer=str(response.content), citations=citations)
