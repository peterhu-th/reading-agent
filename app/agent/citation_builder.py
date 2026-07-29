from app.models.schemas import RetrievedChunk


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
                    f"作者：{chunk.author or 'unknown'}",
                    f"章节：{chunk.chapter_title or chunk.chapter_index}",
                    f"正文：{chunk.text}",
                ]
            )
        )
    return "\n\n".join(blocks)


def build_citations(retrieved: list[RetrievedChunk]) -> list[str]:
    """Build citation strings from retrieved chunk metadata."""
    citations: list[str] = []
    for index, item in enumerate(retrieved, start=1):
        chunk = item.chunk
        chapter = chunk.chapter_title or f"第 {chunk.chapter_index} 章"
        citations.append(
            f"[{index}] {chunk.title} / {chunk.author or 'unknown'} / "
            f"{chapter} / 段落 {chunk.start_paragraph_index}-{chunk.end_paragraph_index}"
        )
    return citations
