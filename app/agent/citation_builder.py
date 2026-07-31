import hashlib

from app.models.schemas import Citation, RetrievedChunk


def source_id_for_chunk(chunk_id: str) -> str:
    """Return a stable opaque source id instead of exposing the chunk id."""

    return hashlib.sha256(chunk_id.encode("utf-8")).hexdigest()[:20]


def build_context(retrieved: list[RetrievedChunk]) -> str:
    """Build numbered context blocks for the answer prompt."""

    blocks: list[str] = []
    for index, item in enumerate(retrieved, start=1):
        chunk = item.chunk
        blocks.append(
            "\n".join(
                [
                    f"[{index}]",
                    f"书名：{chunk.title}",
                    f"作者：{chunk.author or '未知'}",
                    f"章节：{chunk.chapter_title or f'第 {chunk.chapter_index} 章'}",
                    f"正文：{chunk.text}",
                ]
            )
        )
    return "\n\n".join(blocks)


def build_citations(retrieved: list[RetrievedChunk]) -> list[Citation]:
    """Build structured citations from retrieved chunk metadata."""

    citations: list[Citation] = []
    for index, item in enumerate(retrieved, start=1):
        chunk = item.chunk
        chapter = chunk.chapter_title or f"第 {chunk.chapter_index} 章"
        excerpt = " ".join(chunk.text.split())
        if len(excerpt) > 220:
            excerpt = excerpt[:220].rstrip() + "…"
        citations.append(
            Citation(
                source_id=source_id_for_chunk(chunk.chunk_id),
                display_index=index,
                title=chunk.title,
                author=chunk.author,
                chapter_title=chapter,
                paragraph_range=f"{chunk.start_paragraph_index}-{chunk.end_paragraph_index}",
                excerpt=excerpt,
            )
        )
    return citations


def format_citation(citation: Citation) -> str:
    return (
        f"[{citation.display_index}] {citation.title} / {citation.author or '未知'} / "
        f"{citation.chapter_title} / 段落 {citation.paragraph_range}"
    )
