import os
from functools import lru_cache

from dotenv import load_dotenv
from pydantic import BaseModel, Field


load_dotenv()


class Settings(BaseModel):
    """Runtime settings loaded from environment variables."""

    OPENAI_API_KEY: str = Field(min_length=1)
    OPENAI_BASE_URL: str = Field(min_length=1)
    CHAT_MODEL: str = Field(default="gpt-5.4", min_length=1)
    ANSWER_MODEL: str = Field(default="gpt-5.4", min_length=1)
    INTENT_MODEL: str = Field(default="gpt-5.4", min_length=1)
    SUMMARY_MODEL: str = Field(default="gpt-5.4", min_length=1)
    EVIDENCE_CHECK_MODEL: str = Field(default="gpt-5.4", min_length=1)
    AUTO_START_API: bool = True
    AICLIENT2API_DIR: str = "D:/gitstore/AIClient2API"
    API_START_TIMEOUT_SECONDS: int = Field(default=30, ge=1)

    EMBEDDING_BACKEND: str = Field(default="local-hash", min_length=1)
    EMBEDDING_MODEL: str = Field(default="./models/bge-base-zh-v1.5", min_length=1)
    EMBEDDING_DEVICE: str = "cpu"
    EMBEDDING_BATCH_SIZE: int = Field(default=64, ge=1)
    EMBEDDING_LOCAL_FILES_ONLY: bool = True

    RERANK_BACKEND: str = Field(default="lightweight", min_length=1)
    RERANK_MODEL: str = Field(default="./models/bge-reranker-base", min_length=1)
    RERANK_DEVICE: str = "cpu"
    RERANK_CANDIDATE_K: int = Field(default=60, ge=1)

    VECTOR_DB_PATH: str = "./data/index/chroma"
    RAW_EPUB_DIR: str = "./data/raw/epub"
    BOOKS_JSONL_PATH: str = "./data/processed/books_jsonl/books.jsonl"
    READER_JSONL_PATH: str = "./data/processed/reader_jsonl/reader.jsonl"
    ANNOTATIONS_JSON_PATH: str = "./data/user/annotations.json"
    CHUNKS_JSONL_PATH: str = "./data/processed/chunks_jsonl/chunks.jsonl"
    SUMMARIES_JSONL_PATH: str = "./data/processed/summaries_jsonl/summaries.jsonl"
    CHROMA_COLLECTION: str = "reading_memory_chunks"
    SUMMARY_CHROMA_COLLECTION: str = "reading_memory_summaries"

    VECTOR_INITIAL_K: int = Field(default=40, ge=1)
    KEYWORD_INITIAL_K: int = Field(default=40, ge=1)
    RERANK_TOP_K: int = Field(default=15, ge=1)
    FINAL_TOP_K: int = Field(default=10, ge=1)
    CONTEXT_MAX_CHARS: int = Field(default=9000, ge=1000)
    CHUNK_SIZE: int = Field(default=900, ge=100)
    CHUNK_OVERLAP: int = Field(default=180, ge=0)

    RETRIEVAL_MAX_SUPPLEMENT_ROUNDS: int = Field(default=2, ge=0, le=4)
    EVIDENCE_CHECK_MAX_CHARS: int = Field(default=12000, ge=1000)
    EVIDENCE_MIN_CHUNKS: int = Field(default=5, ge=1)
    COMPARISON_MIN_CHUNKS_PER_BOOK: int = Field(default=2, ge=1)

    SUMMARY_MAX_SOURCE_CHARS: int = Field(default=3000, ge=500)
    SUMMARY_CHAPTER_TARGET_CHARS: int = Field(default=200, ge=50)
    SUMMARY_BOOK_TARGET_CHARS: int = Field(default=800, ge=100)
    SUMMARY_BACKEND: str = "extractive"
    SUMMARY_PROMPT_VERSION: str = "summary_v2"
    CONVERSATION_MAX_TURNS: int = Field(default=12, ge=1)
    CONVERSATION_RECENT_TURNS: int = Field(default=4, ge=1)
    CONVERSATION_SUMMARY_INTERVAL: int = Field(default=3, ge=1)
    CONVERSATION_SUMMARY_MAX_CHARS: int = Field(default=600, ge=100)

    WEB_HOST: str = "127.0.0.1"
    WEB_PORT: int = Field(default=8000, ge=1, le=65535)


def env_bool(name: str, default: bool) -> bool:
    value = os.getenv(name)
    if value is None:
        return default
    return value.lower() in {"1", "true", "yes", "on"}


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    """Return cached project settings."""

    chat_model = os.getenv("CHAT_MODEL", "gpt-5.4")
    intent_model = os.getenv("INTENT_MODEL", chat_model)
    return Settings(
        OPENAI_API_KEY=os.getenv("OPENAI_API_KEY", ""),
        OPENAI_BASE_URL=os.getenv("OPENAI_BASE_URL", ""),
        CHAT_MODEL=chat_model,
        ANSWER_MODEL=os.getenv("ANSWER_MODEL", chat_model),
        INTENT_MODEL=intent_model,
        SUMMARY_MODEL=os.getenv("SUMMARY_MODEL", chat_model),
        EVIDENCE_CHECK_MODEL=os.getenv("EVIDENCE_CHECK_MODEL", intent_model),
        AUTO_START_API=env_bool("AUTO_START_API", True),
        AICLIENT2API_DIR=os.getenv("AICLIENT2API_DIR", "D:/gitstore/AIClient2API"),
        API_START_TIMEOUT_SECONDS=int(os.getenv("API_START_TIMEOUT_SECONDS", "30")),
        EMBEDDING_BACKEND=os.getenv("EMBEDDING_BACKEND", "local-hash"),
        EMBEDDING_MODEL=os.getenv("EMBEDDING_MODEL", "./models/bge-base-zh-v1.5"),
        EMBEDDING_DEVICE=os.getenv("EMBEDDING_DEVICE", "cpu"),
        EMBEDDING_BATCH_SIZE=int(os.getenv("EMBEDDING_BATCH_SIZE", "64")),
        EMBEDDING_LOCAL_FILES_ONLY=env_bool("EMBEDDING_LOCAL_FILES_ONLY", True),
        RERANK_BACKEND=os.getenv("RERANK_BACKEND", "lightweight"),
        RERANK_MODEL=os.getenv("RERANK_MODEL", "./models/bge-reranker-base"),
        RERANK_DEVICE=os.getenv("RERANK_DEVICE", "cpu"),
        RERANK_CANDIDATE_K=int(os.getenv("RERANK_CANDIDATE_K", "60")),
        VECTOR_DB_PATH=os.getenv("VECTOR_DB_PATH", "./data/index/chroma"),
        RAW_EPUB_DIR=os.getenv("RAW_EPUB_DIR", "./data/raw/epub"),
        BOOKS_JSONL_PATH=os.getenv("BOOKS_JSONL_PATH", "./data/processed/books_jsonl/books.jsonl"),
        READER_JSONL_PATH=os.getenv("READER_JSONL_PATH", "./data/processed/reader_jsonl/reader.jsonl"),
        ANNOTATIONS_JSON_PATH=os.getenv("ANNOTATIONS_JSON_PATH", "./data/user/annotations.json"),
        CHUNKS_JSONL_PATH=os.getenv("CHUNKS_JSONL_PATH", "./data/processed/chunks_jsonl/chunks.jsonl"),
        SUMMARIES_JSONL_PATH=os.getenv("SUMMARIES_JSONL_PATH", "./data/processed/summaries_jsonl/summaries.jsonl"),
        CHROMA_COLLECTION=os.getenv("CHROMA_COLLECTION", "reading_memory_chunks"),
        SUMMARY_CHROMA_COLLECTION=os.getenv("SUMMARY_CHROMA_COLLECTION", "reading_memory_summaries"),
        VECTOR_INITIAL_K=int(os.getenv("VECTOR_INITIAL_K", "40")),
        KEYWORD_INITIAL_K=int(os.getenv("KEYWORD_INITIAL_K", "40")),
        RERANK_TOP_K=int(os.getenv("RERANK_TOP_K", "15")),
        FINAL_TOP_K=int(os.getenv("FINAL_TOP_K", "10")),
        CONTEXT_MAX_CHARS=int(os.getenv("CONTEXT_MAX_CHARS", "9000")),
        CHUNK_SIZE=int(os.getenv("CHUNK_SIZE", "900")),
        CHUNK_OVERLAP=int(os.getenv("CHUNK_OVERLAP", "180")),
        RETRIEVAL_MAX_SUPPLEMENT_ROUNDS=int(os.getenv("RETRIEVAL_MAX_SUPPLEMENT_ROUNDS", "2")),
        EVIDENCE_CHECK_MAX_CHARS=int(os.getenv("EVIDENCE_CHECK_MAX_CHARS", "12000")),
        EVIDENCE_MIN_CHUNKS=int(os.getenv("EVIDENCE_MIN_CHUNKS", "5")),
        COMPARISON_MIN_CHUNKS_PER_BOOK=int(os.getenv("COMPARISON_MIN_CHUNKS_PER_BOOK", "2")),
        SUMMARY_MAX_SOURCE_CHARS=int(os.getenv("SUMMARY_MAX_SOURCE_CHARS", "3000")),
        SUMMARY_CHAPTER_TARGET_CHARS=int(os.getenv("SUMMARY_CHAPTER_TARGET_CHARS", "200")),
        SUMMARY_BOOK_TARGET_CHARS=int(os.getenv("SUMMARY_BOOK_TARGET_CHARS", "800")),
        SUMMARY_BACKEND=os.getenv("SUMMARY_BACKEND", "extractive"),
        SUMMARY_PROMPT_VERSION=os.getenv("SUMMARY_PROMPT_VERSION", "summary_v2"),
        CONVERSATION_MAX_TURNS=int(os.getenv("CONVERSATION_MAX_TURNS", "12")),
        CONVERSATION_RECENT_TURNS=int(os.getenv("CONVERSATION_RECENT_TURNS", "4")),
        CONVERSATION_SUMMARY_INTERVAL=int(os.getenv("CONVERSATION_SUMMARY_INTERVAL", "3")),
        CONVERSATION_SUMMARY_MAX_CHARS=int(os.getenv("CONVERSATION_SUMMARY_MAX_CHARS", "600")),
        WEB_HOST=os.getenv("WEB_HOST", "127.0.0.1"),
        WEB_PORT=int(os.getenv("WEB_PORT", "8000")),
    )
