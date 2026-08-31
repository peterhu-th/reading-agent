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
    "中国版本图书馆cip",
    "cip数据核字",
    "责任编辑",
    "责任校对",
    "责任印制",
    "印刷者",
    "出版发行",
    "定价：",
    "开本",
    "印张",
)

NON_CONTENT_PATTERNS = (
    re.compile(r"https?://|www\.", re.IGNORECASE),
    re.compile(r"\bISBN[\s:：-]*[0-9Xx-]+", re.IGNORECASE),
    re.compile(r"CIP数据核字", re.IGNORECASE),
    re.compile(r"\b\d{3,4}毫米[×xX]\d{3,4}毫米"),
    re.compile(r"^(网址|邮编|电话|传真|电子邮箱)[：:]"),
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
    if any(pattern.search(normalized) for pattern in NON_CONTENT_PATTERNS):
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


def is_reader_visible_paragraph(text: str) -> bool:
    """Apply reader-facing filtering to both new and already-generated JSONL data."""

    return is_retrievable_paragraph(text, min_length=10)
