import json
from dataclasses import dataclass
from functools import cached_property
from pathlib import Path

from app.agent.answer_generator import generate_answer
from app.agent.citation_builder import source_id_for_chunk
from app.config import get_settings
from app.memory.conversation_memory import maybe_compact_conversation, update_conversation
from app.memory.conversation_resolver import resolve_question
from app.memory.session_store import SessionStore
from app.models.schemas import AnswerWithCitations, BookSummary, ConversationSession, IterativeRetrievalResult, ResolvedQuestion, TextChunk
from app.retrieval.hybrid_retriever import EnhancedRetriever
from app.retrieval.iterative_retriever import IterativeRetriever, StatusCallback


@dataclass
class PreparedTurn:
    session: ConversationSession
    resolved: ResolvedQuestion
    retrieval: IterativeRetrievalResult | None = None


class ReadingAssistantService:
    """Shared application service for CLI and Web clients."""

    def __init__(self, store: SessionStore | None = None) -> None:
        self.store = store or SessionStore()
        self._iterative: IterativeRetriever | None = None

    @property
    def iterative(self) -> IterativeRetriever:
        if self._iterative is None:
            self._iterative = IterativeRetriever()
        return self._iterative

    @cached_property
    def chunks(self) -> list[TextChunk]:
        path = Path(get_settings().CHUNKS_JSONL_PATH)
        if not path.exists():
            return []
        records: list[TextChunk] = []
        with path.open("r", encoding="utf-8") as file:
            for line in file:
                if line.strip():
                    records.append(TextChunk(**json.loads(line)))
        return records

    @cached_property
    def source_chunks(self) -> dict[str, TextChunk]:
        return {source_id_for_chunk(chunk.chunk_id): chunk for chunk in self.chunks}

    def create_session(self) -> ConversationSession:
        return self.store.create()

    def prepare_turn(self, session_id: str, question: str, selected_books: list[str] | None = None, debug: bool = False, status_callback: StatusCallback | None = None) -> PreparedTurn:
        session = self.store.get(session_id)
        selected = list(dict.fromkeys(selected_books or []))
        session = session.model_copy(update={"explicit_book_titles": selected})
        self.store.save(session)
        resolved = resolve_question(question, session, selected)
        if resolved.clarification_needed:
            return PreparedTurn(session=session, resolved=resolved)
        retrieval = self.iterative.search(resolved.standalone_question, session, debug, status_callback)
        return PreparedTurn(session=session, resolved=resolved, retrieval=retrieval)

    def generate(self, prepared: PreparedTurn) -> AnswerWithCitations:
        if prepared.retrieval is None:
            raise ValueError("Cannot generate an answer before retrieval")
        return generate_answer(prepared.resolved.original_question, prepared.retrieval.chunks, prepared.retrieval.intent, prepared.session)

    def finalize_turn(self, prepared: PreparedTurn, answer: AnswerWithCitations) -> ConversationSession:
        if prepared.retrieval is None:
            return prepared.session
        updated = update_conversation(
            prepared.session,
            prepared.resolved.original_question,
            prepared.retrieval,
            answer,
            prepared.resolved.standalone_question,
            prepared.resolved.resolved_entities,
        )
        updated = maybe_compact_conversation(updated)
        self.store.save(updated)
        return updated

    def list_books(self) -> list[BookSummary]:
        grouped: dict[str, dict] = {}
        for chunk in self.chunks:
            item = grouped.setdefault(chunk.book_id, {"book_id": chunk.book_id, "title": chunk.title, "author": chunk.author, "book_type": chunk.book_type, "chapters": set(), "chunk_count": 0})
            item["chapters"].add(chunk.chapter_index)
            item["chunk_count"] += 1
        return sorted(
            [BookSummary(book_id=item["book_id"], title=item["title"], author=item["author"], book_type=item["book_type"], chapter_count=len(item["chapters"]), chunk_count=item["chunk_count"]) for item in grouped.values()],
            key=lambda item: item.title,
        )

    def get_source(self, source_id: str) -> TextChunk | None:
        return self.source_chunks.get(source_id)
