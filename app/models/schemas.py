from pydantic import BaseModel, Field


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
    chapter_index: int = Field(ge=0)
    chapter_title: str = ""
    chunk_index: int = Field(ge=0)
    start_paragraph_index: int = Field(ge=0)
    end_paragraph_index: int = Field(ge=0)
    text: str = Field(min_length=1)


class RetrievedChunk(BaseModel):
    """A chunk returned by retrieval, optionally with a similarity score."""

    chunk: TextChunk
    score: float | None = None
    vector_score: float | None = None
    keyword_score: float | None = None
    rerank_score: float | None = None
    matched_queries: list[str] = Field(default_factory=list)
    debug_reason: str = ""


class IntentAnalysis(BaseModel):
    """Structured interpretation of a user question before retrieval."""

    labels: list[str] = Field(default_factory=list)
    need_types: list[str] = Field(default_factory=list)
    book_titles: list[str] = Field(default_factory=list)
    authors: list[str] = Field(default_factory=list)
    topics: list[str] = Field(default_factory=list)
    emotions: list[str] = Field(default_factory=list)
    question: str = Field(min_length=1)
    complexity: str = "normal"
    confidence: float = Field(default=0.0, ge=0.0, le=1.0)


class PlannedQuery(BaseModel):
    """One concrete retrieval query plus optional metadata filters."""

    query: str = Field(min_length=1)
    metadata_filter: dict[str, str | list[str]] = Field(default_factory=dict)
    purpose: str = ""


class RetrievalPlan(BaseModel):
    """Retrieval parameters derived from intent analysis."""

    queries: list[PlannedQuery] = Field(default_factory=list)
    vector_initial_k: int = Field(default=40, ge=1)
    keyword_initial_k: int = Field(default=40, ge=1)
    rerank_top_k: int = Field(default=15, ge=1)
    final_top_k: int = Field(default=10, ge=1)
    neighbor_window: int = Field(default=1, ge=0)
    context_max_chars: int = Field(default=9000, ge=1000)


class RetrievalDebugInfo(BaseModel):
    """Human-readable retrieval diagnostics for CLI debug mode."""

    candidate_count: int = 0
    reranked_count: int = 0
    expanded_count: int = 0
    final_count: int = 0
    lines: list[str] = Field(default_factory=list)


class RetrievalResult(BaseModel):
    """Final retrieval result plus planning/debug data."""

    chunks: list[RetrievedChunk] = Field(default_factory=list)
    intent: IntentAnalysis
    plan: RetrievalPlan
    debug: RetrievalDebugInfo = Field(default_factory=RetrievalDebugInfo)


class AnswerWithCitations(BaseModel):
    """The final generated answer plus program-built citation strings."""

    answer: str = Field(min_length=1)
    citations: list[str] = Field(default_factory=list)
