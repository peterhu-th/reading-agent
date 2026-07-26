import os
from functools import lru_cache

from dotenv import load_dotenv
from pydantic import BaseModel, Field

# 把 .env 文件内容加载进当前程序的环境变量
load_dotenv()


class Settings(BaseModel):
    """Runtime settings loaded from environment variables."""

    OPENAI_API_KEY: str = Field(min_length=1)
    OPENAI_BASE_URL: str = Field(min_length=1)
    CHAT_MODEL: str = Field(default="gpt-5.4", min_length=1)
    AUTO_START_API: bool = True
    AICLIENT2API_DIR: str = "D:/gitstore/AIClient2API"
    API_START_TIMEOUT_SECONDS: int = Field(default=30, ge=1)
    EMBEDDING_BACKEND: str = Field(default="local-hash", min_length=1)
    EMBEDDING_MODEL: str = Field(default="local-hash", min_length=1)
    EMBEDDING_DEVICE: str = "cpu"
    EMBEDDING_BATCH_SIZE: int = Field(default=64, ge=1)
    EMBEDDING_LOCAL_FILES_ONLY: bool = True
    # 向量索引文件
    VECTOR_DB_PATH: str = "./data/index/chroma"
    RAW_EPUB_DIR: str = "./data/raw/epub"
    BOOKS_JSONL_PATH: str = "./data/processed/books_jsonl/books.jsonl"
    CHUNKS_JSONL_PATH: str = "./data/processed/chunks_jsonl/chunks.jsonl"
    CHROMA_COLLECTION: str = "reading_memory_chunks"
    VECTOR_INITIAL_K: int = Field(default=40, ge=1)
    KEYWORD_INITIAL_K: int = Field(default=40, ge=1)
    RERANK_TOP_K: int = Field(default=15, ge=1)
    FINAL_TOP_K: int = Field(default=10, ge=1)
    CONTEXT_MAX_CHARS: int = Field(default=9000, ge=1000)
    CHUNK_SIZE: int = Field(default=900, ge=100)
    CHUNK_OVERLAP: int = Field(default=180, ge=0)

# 缓存装饰器：第一次运行创建 Setting 对象，后续调用直接返回创建好的对象
@lru_cache(maxsize=1)
def get_settings() -> Settings:
    """Return cached project settings."""

    return Settings(
        OPENAI_API_KEY=os.getenv("OPENAI_API_KEY", ""),
        OPENAI_BASE_URL=os.getenv("OPENAI_BASE_URL", ""),
        CHAT_MODEL=os.getenv("CHAT_MODEL", "gpt-5.4"),
        AUTO_START_API=os.getenv("AUTO_START_API", "true").lower() in {"1", "true", "yes", "on"},
        AICLIENT2API_DIR=os.getenv("AICLIENT2API_DIR", "D:/gitstore/AIClient2API"),
        API_START_TIMEOUT_SECONDS=int(os.getenv("API_START_TIMEOUT_SECONDS", "30")),
        EMBEDDING_BACKEND=os.getenv("EMBEDDING_BACKEND", "local-hash"),
        EMBEDDING_MODEL=os.getenv("EMBEDDING_MODEL", "local-hash"),
        EMBEDDING_DEVICE=os.getenv("EMBEDDING_DEVICE", "cpu"),
        EMBEDDING_BATCH_SIZE=int(os.getenv("EMBEDDING_BATCH_SIZE", "64")),
        EMBEDDING_LOCAL_FILES_ONLY=os.getenv("EMBEDDING_LOCAL_FILES_ONLY", "true").lower()
        in {"1", "true", "yes", "on"},
        VECTOR_DB_PATH=os.getenv("VECTOR_DB_PATH", "./data/index/chroma"),
        RAW_EPUB_DIR=os.getenv("RAW_EPUB_DIR", "./data/raw/epub"),
        BOOKS_JSONL_PATH=os.getenv(
            "BOOKS_JSONL_PATH",
            "./data/processed/books_jsonl/books.jsonl",
        ),
        CHUNKS_JSONL_PATH=os.getenv(
            "CHUNKS_JSONL_PATH",
            "./data/processed/chunks_jsonl/chunks.jsonl",
        ),
        CHROMA_COLLECTION=os.getenv("CHROMA_COLLECTION", "reading_memory_chunks"),
        VECTOR_INITIAL_K=int(os.getenv("VECTOR_INITIAL_K", "40")),
        KEYWORD_INITIAL_K=int(os.getenv("KEYWORD_INITIAL_K", "40")),
        RERANK_TOP_K=int(os.getenv("RERANK_TOP_K", "15")),
        FINAL_TOP_K=int(os.getenv("FINAL_TOP_K", "10")),
        CONTEXT_MAX_CHARS=int(os.getenv("CONTEXT_MAX_CHARS", "9000")),
        CHUNK_SIZE=int(os.getenv("CHUNK_SIZE", "900")),
        CHUNK_OVERLAP=int(os.getenv("CHUNK_OVERLAP", "180")),
    )
