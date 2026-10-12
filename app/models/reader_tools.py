"""Book-scoped reading tools: request, response and persisted bookmark models."""

from typing import Generic, Literal, TypeVar
from uuid import uuid4

from pydantic import BaseModel, ConfigDict, Field, model_validator

from app.models.schemas import (
    EditorState,
    ReaderAnnotation,
    ReaderBook,
    ReaderLocation,
    ReaderSearchHit,
    utc_now_iso,
)


T = TypeVar("T")
AnnotationColor = Literal["yellow", "green", "blue", "red"]


class PaginatedResponse(BaseModel, Generic[T]):
    items: list[T]
    total: int = Field(ge=0)
    offset: int = Field(ge=0)
    limit: int = Field(ge=1, le=100)
    next_offset: int | None = Field(default=None, ge=0)


class AnnotationCreateInput(BaseModel):
    model_config = ConfigDict(extra="forbid")

    chapter_index: int = Field(ge=0)
    start_edit_id: str = Field(min_length=1, max_length=128)
    end_edit_id: str = Field(min_length=1, max_length=128)
    start_offset: int = Field(default=0, ge=0)
    end_offset: int = Field(default=0, ge=0)
    selected_text: str = Field(default="", max_length=50000)
    color: AnnotationColor = "yellow"
    comment: str = Field(default="", max_length=10000)


class AnnotationUpdateInput(BaseModel):
    model_config = ConfigDict(extra="forbid")

    comment: str | None = Field(default=None, max_length=10000)
    color: AnnotationColor | None = None

    @model_validator(mode="after")
    def require_changes(self):
        if not self.model_fields_set:
            raise ValueError("请提供 comment 或 color。")
        if any(getattr(self, field) is None for field in self.model_fields_set):
            raise ValueError("批注字段不能为 null；清空备注请使用空字符串。")
        return self


class ReaderBookmark(BaseModel):
    bookmark_id: str = Field(default_factory=lambda: uuid4().hex)
    book_id: str = Field(min_length=1)
    chapter_index: int = Field(ge=0)
    edit_id: str = Field(min_length=1)
    label: str = Field(default="", max_length=200)
    created_at: str = Field(default_factory=utc_now_iso)


class BookmarkCreateInput(BaseModel):
    model_config = ConfigDict(extra="forbid")

    chapter_index: int = Field(ge=0)
    edit_id: str = Field(min_length=1, max_length=128)
    label: str = Field(default="", max_length=200)


class AnnotationListItem(ReaderAnnotation):
    chapter_title: str = ""
    excerpt: str = ""
    quote: str = ""
    reader_location: ReaderLocation | None = None


class BookmarkListItem(ReaderBookmark):
    chapter_title: str = ""
    excerpt: str = ""
    quote: str = ""
    reader_location: ReaderLocation | None = None


class SearchResultItem(ReaderSearchHit):
    book_id: str
    edit_id: str
    reader_location: ReaderLocation


class AnnotationMutationResponse(BaseModel):
    annotation: ReaderAnnotation
    state: EditorState


class BookmarkMutationResponse(BaseModel):
    bookmark: ReaderBookmark
    state: EditorState


class NotesExport(BaseModel):
    book: ReaderBook
    exported_at: str = Field(default_factory=utc_now_iso)
    has_unsaved_changes: bool = Field(description="Whether the shared editing session has unsaved changes.")
    annotations: list[AnnotationListItem]
    bookmarks: list[BookmarkListItem]


class ApiError(BaseModel):
    detail: str


class ValidationIssue(BaseModel):
    loc: list[str | int]
    msg: str
    type: str


class ApiValidationError(BaseModel):
    detail: str | list[ValidationIssue]
