import re
from collections import defaultdict
from dataclasses import dataclass

from app.models.schemas import BookParagraph, TextChunk


SENTENCE_END_PATTERN = re.compile(r"[。！？；.!?;][”’」』）】》]*")


@dataclass(frozen=True)
class ChunkingPolicy:
    book_type: str
    chunk_size: int
    overlap: int
    min_chunk_chars: int


BOOK_TYPE_KEYWORDS: dict[str, tuple[str, ...]] = {
    "poetry": ("诗经", "海子的诗", "张枣的诗"),
    "history": ("史记",),
    "philosophy": ("理想国", "查拉图斯特拉", "西西弗", "疯癫与文明", "瓦尔登湖"),
}

POLICIES: dict[str, ChunkingPolicy] = {
    "fiction": ChunkingPolicy("fiction", chunk_size=900, overlap=180, min_chunk_chars=220),
    "poetry": ChunkingPolicy("poetry", chunk_size=420, overlap=0, min_chunk_chars=40),
    "philosophy": ChunkingPolicy("philosophy", chunk_size=760, overlap=160, min_chunk_chars=180),
    "history": ChunkingPolicy("history", chunk_size=820, overlap=120, min_chunk_chars=160),
}


def infer_book_type(title: str) -> str:
    for book_type, keywords in BOOK_TYPE_KEYWORDS.items():
        if any(keyword in title for keyword in keywords):
            return book_type
    return "fiction"


def policy_for_paragraphs(
    paragraphs: list[BookParagraph],
    default_chunk_size: int,
    default_overlap: int,
) -> ChunkingPolicy:
    book_type = infer_book_type(paragraphs[0].title)
    policy = POLICIES.get(book_type, POLICIES["fiction"])
    if default_chunk_size != 900 or default_overlap != 180:
        return ChunkingPolicy(
            book_type=book_type,
            chunk_size=default_chunk_size,
            overlap=default_overlap if book_type != "poetry" else 0,
            min_chunk_chars=policy.min_chunk_chars,
        )
    return policy


def group_paragraphs(
    paragraphs: list[BookParagraph],
) -> dict[tuple[str, int], list[BookParagraph]]:
    """Group paragraphs by book and chapter so chunks keep clear provenance."""
    grouped: dict[tuple[str, int], list[BookParagraph]] = defaultdict(list)
    for paragraph in paragraphs:
        grouped[(paragraph.book_id, paragraph.chapter_index)].append(paragraph)

    for key in grouped:
        grouped[key].sort(key=lambda item: item.paragraph_index)

    return dict(grouped)


def make_chunk(
    paragraphs: list[BookParagraph],
    chunk_index: int,
    text: str,
    book_type: str,
) -> TextChunk:
    """Create one chunk from consecutive paragraphs."""
    if not paragraphs:
        raise ValueError("paragraphs must not be empty")

    first = paragraphs[0]
    last = paragraphs[-1]
    return TextChunk(
        chunk_id=f"{first.book_id}:{first.chapter_index}:{chunk_index}",
        book_id=first.book_id,
        title=first.title,
        author=first.author,
        book_type=book_type,
        chapter_index=first.chapter_index,
        chapter_title=first.chapter_title,
        chunk_index=chunk_index,
        start_paragraph_index=first.paragraph_index,
        end_paragraph_index=last.paragraph_index,
        text=text,
    )


def split_long_paragraph(paragraph: BookParagraph, chunk_size: int) -> list[BookParagraph]:
    """Split one oversized paragraph near sentence boundaries."""
    parts: list[BookParagraph] = []
    start = 0
    text_value = paragraph.text
    while start < len(text_value):
        end = choose_split_end(text_value, start, chunk_size)
        text = text_value[start:end].strip()
        if not text:
            break
        parts.append(
            paragraph.model_copy(
                update={
                    "paragraph_index": paragraph.paragraph_index * 1000 + len(parts),
                    "text": text,
                }
            )
        )
        start = end
    return parts


def choose_split_end(text: str, start: int, chunk_size: int) -> int:
    hard_end = min(start + chunk_size, len(text))
    if hard_end >= len(text):
        return len(text)

    window = text[start:hard_end]
    matches = list(SENTENCE_END_PATTERN.finditer(window))
    if matches:
        boundary = start + matches[-1].end()
        if boundary > start:
            return boundary
    return hard_end


def paragraph_text(paragraphs: list[BookParagraph]) -> str:
    return "\n".join(paragraph.text for paragraph in paragraphs)


def overlap_tail(paragraphs: list[BookParagraph], overlap: int) -> list[BookParagraph]:
    """Return whole trailing paragraphs whose combined text is near overlap."""
    if overlap <= 0:
        return []

    selected: list[BookParagraph] = []
    total = 0
    for paragraph in reversed(paragraphs):
        selected.insert(0, paragraph)
        total += len(paragraph.text) + (1 if selected else 0)
        if total >= overlap:
            break
    return selected


def merge_short_tail(
    chunks: list[TextChunk],
    current_paragraphs: list[BookParagraph],
    current_text: str,
    policy: ChunkingPolicy,
) -> None:
    if not current_paragraphs:
        return
    if chunks and len(current_text) < policy.min_chunk_chars and policy.book_type != "poetry":
        previous = chunks.pop()
        merged_text = f"{previous.text}\n{current_text}"
        merged_paragraphs = [
            current_paragraphs[0].model_copy(
                update={"paragraph_index": previous.start_paragraph_index, "text": merged_text}
            ),
            current_paragraphs[-1],
        ]
        chunks.append(make_chunk(merged_paragraphs, len(chunks), merged_text, policy.book_type))
        return
    chunks.append(make_chunk(current_paragraphs, len(chunks), current_text, policy.book_type))


def chunk_chapter(
    paragraphs: list[BookParagraph],
    policy: ChunkingPolicy,
) -> list[TextChunk]:
    """Merge paragraphs from one chapter into retrievable chunks."""
    if not paragraphs:
        return []
    if policy.chunk_size <= 0:
        raise ValueError("chunk_size must be greater than 0")
    if policy.overlap < 0:
        raise ValueError("overlap must not be negative")

    expanded_paragraphs: list[BookParagraph] = []
    for paragraph in paragraphs:
        if len(paragraph.text) > policy.chunk_size:
            expanded_paragraphs.extend(split_long_paragraph(paragraph, policy.chunk_size))
        else:
            expanded_paragraphs.append(paragraph)

    chunks: list[TextChunk] = []
    current_paragraphs: list[BookParagraph] = []
    current_text = ""

    for paragraph in expanded_paragraphs:
        candidate = paragraph.text if not current_text else f"{current_text}\n{paragraph.text}"
        if current_text and len(candidate) > policy.chunk_size:
            chunks.append(make_chunk(current_paragraphs, len(chunks), current_text, policy.book_type))
            current_paragraphs = overlap_tail(current_paragraphs, policy.overlap)
            current_paragraphs.append(paragraph)
            current_text = paragraph_text(current_paragraphs)
        else:
            current_paragraphs.append(paragraph)
            current_text = candidate

    merge_short_tail(chunks, current_paragraphs, current_text, policy)
    return chunks


def chunk_paragraphs(
    paragraphs: list[BookParagraph],
    chunk_size: int = 900,
    overlap: int = 180,
) -> list[TextChunk]:
    """Chunk all paragraphs with book-type-specific policies."""
    chunks: list[TextChunk] = []
    grouped = group_paragraphs(paragraphs)

    for key in sorted(grouped):
        group = grouped[key]
        chunks.extend(
            chunk_chapter(
                group,
                policy=policy_for_paragraphs(group, chunk_size, overlap),
            )
        )

    return chunks
