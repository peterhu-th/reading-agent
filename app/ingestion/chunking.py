from collections import defaultdict
import re

from app.models.schemas import BookParagraph, TextChunk


SENTENCE_END_PATTERN = re.compile(r"[。！？；.!?;][”’」』）】》]*")


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


def chunk_chapter(
    paragraphs: list[BookParagraph],
    chunk_size: int = 600,
    overlap: int = 100,
) -> list[TextChunk]:
    """Merge paragraphs from one chapter into retrievable chunks."""
    if not paragraphs:
        return []
    if chunk_size <= 0:
        raise ValueError("chunk_size must be greater than 0")
    if overlap < 0:
        raise ValueError("overlap must not be negative")

    expanded_paragraphs: list[BookParagraph] = []
    for paragraph in paragraphs:
        if len(paragraph.text) > chunk_size:
            expanded_paragraphs.extend(split_long_paragraph(paragraph, chunk_size))
        else:
            expanded_paragraphs.append(paragraph)

    chunks: list[TextChunk] = []
    current_paragraphs: list[BookParagraph] = []
    current_text = ""

    for paragraph in expanded_paragraphs:
        candidate = paragraph.text if not current_text else f"{current_text}\n{paragraph.text}"
        if current_text and len(candidate) > chunk_size:
            chunks.append(make_chunk(current_paragraphs, len(chunks), current_text))
            current_paragraphs = overlap_tail(current_paragraphs, overlap)
            current_paragraphs.append(paragraph)
            current_text = paragraph_text(current_paragraphs)
        else:
            current_paragraphs.append(paragraph)
            current_text = candidate

    if current_paragraphs:
        chunks.append(make_chunk(current_paragraphs, len(chunks), current_text))

    return chunks


def chunk_paragraphs(
    paragraphs: list[BookParagraph],
    chunk_size: int = 600,
    overlap: int = 100,
) -> list[TextChunk]:
    """Chunk all paragraphs without crossing book/chapter boundaries."""
    chunks: list[TextChunk] = []
    grouped = group_paragraphs(paragraphs)

    for key in sorted(grouped):
        chunks.extend(
            chunk_chapter(
                grouped[key],
                chunk_size=chunk_size,
                overlap=overlap,
            )
        )

    return chunks
