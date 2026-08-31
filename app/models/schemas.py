from datetime import datetime, timezone
from uuid import uuid4

from pydantic import BaseModel, Field


def utc_now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


class BookParagraph(BaseModel):
    """A normalized paragraph extracted from one EPUB document."""

    book_id: str = Field(min_length=1)
    title: str = Field(min_length=1)
    author: str = ""
    source_path: str = Field(min_length=1)
    chapter_index: int = Field(ge=0)
    chapter_title: str = ""
    paragraph_index: int = Field(ge=0)
    text: str = Field(min_length=1)


class TextChunk(BaseModel):
    """A retrievable text chunk with enough metadata for citation."""

    chunk_id: str = Field(min_length=1)
    book_id: str = Field(min_length=1)
    title: str = Field(min_length=1)
    author: str = ""
    book_type: str = "fiction"
    chapter_index: int = Field(ge=0)
    chapter_title: str = ""
    chunk_index: int = Field(ge=0)
    start_paragraph_index: int = Field(ge=0)
    end_paragraph_index: int = Field(ge=0)
    text: str = Field(min_length=1)


class RetrievedChunk(BaseModel):
    """A chunk returned by retrieval, with optional source scores."""

    chunk: TextChunk
    score: float | None = None
    vector_score: float | None = None
    keyword_score: float | None = None
    cross_encoder_score: float | None = None
    rerank_score: float | None = None
    matched_queries: list[str] = Field(default_factory=list)
    debug_reason: str = ""


class EvidenceRequirement(BaseModel):
    """One independently verifiable evidence need for a question."""

    requirement_id: str = Field(default_factory=lambda: uuid4().hex[:12])
    description: str = Field(min_length=1)
    query: str = Field(min_length=1)
    target_books: list[str] = Field(default_factory=list)
    purpose: str = "evidence"


class IntentAnalysis(BaseModel):
    """Structured interpretation of a user question before retrieval."""

    labels: list[str] = Field(default_factory=list)
    need_types: list[str] = Field(default_factory=list)
    book_titles: list[str] = Field(default_factory=list)
    authors: list[str] = Field(default_factory=list)
    topics: list[str] = Field(default_factory=list)
    emotions: list[str] = Field(default_factory=list)
    evidence_requirements: list[EvidenceRequirement] = Field(default_factory=list)
    question: str = Field(min_length=1)
    complexity: str = "normal"
    confidence: float = Field(default=0.0, ge=0.0, le=1.0)


class PlannedQuery(BaseModel):
    """One concrete retrieval query plus optional metadata filters."""

    query: str = Field(min_length=1)
    metadata_filter: dict[str, str | int | list[str] | list[int]] = Field(default_factory=dict)
    purpose: str = ""


class RetrievalPlan(BaseModel):
    """Retrieval parameters derived from intent analysis."""

    queries: list[PlannedQuery] = Field(default_factory=list)
    vector_initial_k: int = Field(default=40, ge=1)
    keyword_initial_k: int = Field(default=40, ge=1)
    rerank_candidate_k: int = Field(default=60, ge=1)
    rerank_top_k: int = Field(default=15, ge=1)
    final_top_k: int = Field(default=10, ge=1)
    neighbor_window: int = Field(default=1, ge=0)
    context_max_chars: int = Field(default=9000, ge=1000)
    use_summary_index: bool = False


class RetrievalDebugInfo(BaseModel):
    """Human-readable retrieval diagnostics."""

    candidate_count: int = 0
    summary_candidate_count: int = 0
    reranked_count: int = 0
    expanded_count: int = 0
    final_count: int = 0
    reranker_backend: str = "lightweight"
    lines: list[str] = Field(default_factory=list)


class RetrievalResult(BaseModel):
    """Final retrieval result plus planning and debug data."""

    chunks: list[RetrievedChunk] = Field(default_factory=list)
    intent: IntentAnalysis
    plan: RetrievalPlan
    debug: RetrievalDebugInfo = Field(default_factory=RetrievalDebugInfo)


class EvidenceAssessment(BaseModel):
    """Coverage check produced after one retrieval round."""

    sufficient: bool = False
    covered_requirement_ids: list[str] = Field(default_factory=list)
    missing_aspects: list[str] = Field(default_factory=list)
    supplemental_queries: list[PlannedQuery] = Field(default_factory=list)
    reason: str = ""
    used_llm: bool = False


class RetrievalRound(BaseModel):
    """One initial or supplemental retrieval round."""

    round_index: int = Field(ge=0)
    queries: list[PlannedQuery] = Field(default_factory=list)
    candidate_count: int = 0
    new_chunk_count: int = 0
    assessment: EvidenceAssessment | None = None
    stop_reason: str = ""


class IterativeRetrievalResult(BaseModel):
    """Evidence returned after the retrieve-check-supplement loop."""

    chunks: list[RetrievedChunk] = Field(default_factory=list)
    intent: IntentAnalysis
    plan: RetrievalPlan
    assessment: EvidenceAssessment
    rounds: list[RetrievalRound] = Field(default_factory=list)
    debug: RetrievalDebugInfo = Field(default_factory=RetrievalDebugInfo)


class Citation(BaseModel):
    """Public, structured citation without exposing an internal chunk id."""

    source_id: str = Field(min_length=1)
    display_index: int = Field(ge=1)
    title: str = Field(min_length=1)
    author: str = ""
    chapter_title: str = ""
    paragraph_range: str = ""
    excerpt: str = ""
    reader_location: "ReaderLocation | None" = None


class AnswerWithCitations(BaseModel):
    """The final generated answer plus structured source citations."""

    answer: str = Field(min_length=1)
    citations: list[Citation] = Field(default_factory=list)


class ConversationTurn(BaseModel):
    """One completed user-assistant exchange kept in a runtime session."""

    user_question: str = Field(min_length=1)
    resolved_question: str = ""
    assistant_answer: str = ""
    answer_summary: str = ""
    citations: list[Citation] = Field(default_factory=list)
    book_titles: list[str] = Field(default_factory=list)
    authors: list[str] = Field(default_factory=list)
    entities: list[str] = Field(default_factory=list)
    topics: list[str] = Field(default_factory=list)
    missing_aspects: list[str] = Field(default_factory=list)


class ConversationSession(BaseModel):
    """In-memory session state used by CLI and Web clients."""

    session_id: str = Field(default_factory=lambda: uuid4().hex)
    title: str = "新对话"
    turns: list[ConversationTurn] = Field(default_factory=list)
    active_book_titles: list[str] = Field(default_factory=list)
    active_authors: list[str] = Field(default_factory=list)
    active_entities: list[str] = Field(default_factory=list)
    active_topics: list[str] = Field(default_factory=list)
    explicit_book_titles: list[str] = Field(default_factory=list)
    rolling_summary: str = ""
    last_resolved_question: str = ""
    unresolved_references: list[str] = Field(default_factory=list)
    created_at: str = Field(default_factory=utc_now_iso)
    updated_at: str = Field(default_factory=utc_now_iso)


class ResolvedQuestion(BaseModel):
    """A follow-up question rewritten into a standalone retrieval query."""

    original_question: str = Field(min_length=1)
    standalone_question: str = Field(min_length=1)
    inherited_book_titles: list[str] = Field(default_factory=list)
    resolved_entities: list[str] = Field(default_factory=list)
    confidence: float = Field(default=1.0, ge=0.0, le=1.0)
    clarification_needed: bool = False
    clarification_message: str = ""


class AnswerStrategy(BaseModel):
    """Answer shape selected from intent and retrieval evidence."""

    name: str = "explain"
    min_chars: int = Field(default=400, ge=0)
    max_chars: int = Field(default=800, ge=100)
    instruction: str = ""


class SummaryRecord(BaseModel):
    """A chapter-level or book-level summary built from chunk data."""

    summary_id: str = Field(min_length=1)
    summary_type: str = Field(pattern="^(chapter|book)$")
    book_id: str = Field(min_length=1)
    title: str = Field(min_length=1)
    author: str = ""
    chapter_index: int | None = None
    chapter_title: str = ""
    source_chunk_ids: list[str] = Field(default_factory=list)
    source_hash: str = Field(min_length=1)
    summary_model: str = Field(min_length=1)
    prompt_version: str = Field(min_length=1)
    text: str = Field(min_length=1)


class RerankDebugInfo(BaseModel):
    """Machine-readable reranker diagnostics for tests and debug output."""

    backend: str = "lightweight"
    used_cross_encoder: bool = False
    fallback_reason: str = ""


class BookSummary(BaseModel):
    """Public book metadata derived from processed chunks."""

    book_id: str
    title: str
    author: str = ""
    book_type: str = "fiction"
    chapter_count: int = 0
    chunk_count: int = 0


class ReaderLocation(BaseModel):
    """Stable location used to navigate from an answer citation into the reader."""

    book_id: str = Field(min_length=1)
    chapter_index: int = Field(ge=0)
    start_paragraph_index: int = Field(ge=0)
    end_paragraph_index: int = Field(ge=0)


Citation.model_rebuild()


class ReaderBook(BaseModel):
    book_id: str = Field(min_length=1)
    title: str = Field(min_length=1)
    author: str = ""
    book_type: str = "fiction"
    chapter_count: int = Field(default=0, ge=0)
    paragraph_count: int = Field(default=0, ge=0)


class ReaderChapter(BaseModel):
    book_id: str = Field(min_length=1)
    chapter_index: int = Field(ge=0)
    chapter_title: str = ""
    depth: int = Field(default=0, ge=0)
    paragraph_count: int = Field(default=0, ge=0)
    preview: str = ""


class ReaderParagraph(BaseModel):
    paragraph_index: int = Field(ge=0)
    edit_id: str = ""
    text: str = Field(min_length=1)
    kind: str = Field(default="paragraph", pattern="^(paragraph|heading|verse|quote|list)$")
    annotations: list["ReaderAnnotation"] = Field(default_factory=list)


class ReaderContentRecord(BaseModel):
    """Lossless text record used only by the EPUB reader, never by RAG retrieval."""

    book_id: str = Field(min_length=1)
    title: str = Field(min_length=1)
    author: str = ""
    book_type: str = "fiction"
    chapter_index: int = Field(ge=0)
    chapter_title: str = ""
    chapter_depth: int = Field(default=0, ge=0)
    paragraph_index: int = Field(ge=0)
    text: str = Field(min_length=1)
    kind: str = Field(default="paragraph", pattern="^(paragraph|heading|verse|quote|list)$")
    source_epub: str = ""
    source_href: str = ""
    source_node_path: str = ""
    source_line_index: int = Field(default=0, ge=0)


class ReaderAnnotation(BaseModel):
    annotation_id: str = Field(default_factory=lambda: uuid4().hex)
    book_id: str = Field(min_length=1)
    chapter_index: int = Field(ge=0)
    start_edit_id: str = Field(min_length=1)
    end_edit_id: str = Field(min_length=1)
    start_offset: int = Field(default=0, ge=0)
    end_offset: int = Field(default=0, ge=0)
    selected_text: str = ""
    color: str = Field(default="yellow", pattern="^(yellow|green|blue|red)$")
    comment: str = ""
    created_at: str = Field(default_factory=utc_now_iso)
    updated_at: str = Field(default_factory=utc_now_iso)


class ReaderMutation(BaseModel):
    edit_id: str = Field(min_length=1)
    text: str = ""
    deleted: bool = False


class ReaderOperationRequest(BaseModel):
    book_id: str = Field(min_length=1)
    label: str = "编辑正文"
    mutations: list[ReaderMutation] = Field(min_length=1)


class ChapterRenameRequest(BaseModel):
    title: str = Field(min_length=1, max_length=300)


class AnnotationCreateRequest(BaseModel):
    book_id: str = Field(min_length=1)
    chapter_index: int = Field(ge=0)
    start_edit_id: str = Field(min_length=1)
    end_edit_id: str = Field(min_length=1)
    start_offset: int = Field(default=0, ge=0)
    end_offset: int = Field(default=0, ge=0)
    selected_text: str = ""
    color: str = Field(default="yellow", pattern="^(yellow|green|blue|red)$")
    comment: str = ""


class EditorState(BaseModel):
    dirty: bool = False
    undo_count: int = Field(default=0, ge=0)
    last_operation: str = ""
    database_updating: bool = False
    database_status: str = "idle"
    database_message: str = ""


ReaderParagraph.model_rebuild()


class ReaderPage(BaseModel):
    book: ReaderBook
    chapter: ReaderChapter
    paragraphs: list[ReaderParagraph] = Field(default_factory=list)
    offset: int = Field(default=0, ge=0)
    next_offset: int | None = None
    previous_chapter_index: int | None = None
    next_chapter_index: int | None = None


class ReaderSearchHit(BaseModel):
    chapter_index: int = Field(ge=0)
    chapter_title: str = ""
    paragraph_index: int = Field(ge=0)
    excerpt: str = ""


class ChatRequest(BaseModel):
    session_id: str = Field(min_length=1)
    question: str = Field(min_length=1)
    selected_books: list[str] = Field(default_factory=list)
    scope: str = Field(default="library", pattern="^(library|book|chapter)$")
    book_id: str = ""
    book_title: str = ""
    chapter_index: int | None = Field(default=None, ge=0)
    selected_text: str = Field(default="", max_length=4000)
    selected_paragraphs: list[int] = Field(default_factory=list)
    debug: bool = False
