import re


NOTE_PREFIXES = (
    "校注：",
    "译注：",
    "注：",
    "注释：",
    "脚注：",
    "尾注：",
    "编注：",
)
NON_CONTENT_EXACT = {
    "目录",
    "版权",
    "封面",
    "扉页",
    "描述",
}
NON_CONTENT_PREFIXES = (
    "版权所有",
    "copyright",
    "isbn",
    "本书由",
    "图书在版",
)


def normalize_whitespace(text: str) -> str:
    text = re.sub(r"[\ufffd\ue000-\uf8ff]", "", text)
    return re.sub(r"\s+", " ", text).strip()


def is_useful_paragraph(text: str, min_length: int = 10) -> bool:
    normalized = normalize_whitespace(text)
    return len(normalized) >= min_length


def is_retrievable_paragraph(text: str, min_length: int = 20) -> bool:
    """Return whether a paragraph should enter the main RAG index."""
    normalized = normalize_whitespace(text)
    if len(normalized) < min_length:
        return False

    lowered = normalized.lower()
    if normalized in NON_CONTENT_EXACT:
        return False
    if any(lowered.startswith(prefix) for prefix in NON_CONTENT_PREFIXES):
        return False
    if any(normalized.startswith(prefix) for prefix in NOTE_PREFIXES):
        return False
    compact = re.sub(r"\s+", "", normalized)
    if re.match(r"^(校注|译注|脚注|尾注|编注)[：:；;]", compact):
        return False
    if compact.startswith("文档制作者按"):
        return False
    if re.fullmatch(r"[［\[]?\d{1,4}[］\]]?", normalized):
        return False
    if re.fullmatch(r"[ivxlcdmIVXLCDM]{1,12}", normalized):
        return False
    return True
