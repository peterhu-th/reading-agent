"""Typed, book-scoped REST endpoints for the reader's tools."""

from contextlib import contextmanager
from typing import Annotated, Literal
from urllib.parse import quote

from fastapi import APIRouter, HTTPException, Query, Response

from app.models.reader_tools import (
    AnnotationCreateInput,
    AnnotationListItem,
    AnnotationMutationResponse,
    AnnotationUpdateInput,
    ApiError,
    ApiValidationError,
    BookmarkCreateInput,
    BookmarkListItem,
    BookmarkMutationResponse,
    NotesExport,
    PaginatedResponse,
    SearchResultItem,
)
from app.models.schemas import AnnotationCreateRequest, EditorState, ReaderLocation
from app.services.editor_session import EditorSessionService
from app.services.reader import ReaderNotFoundError, ReaderService
from app.services.reading_notes import ReadingNotesService


Offset = Annotated[int, Query(ge=0)]
Limit = Annotated[int, Query(ge=1, le=100)]
Chapter = Annotated[int | None, Query(ge=0)]


@contextmanager
def reader_errors():
    try:
        yield
    except ReaderNotFoundError as exc:
        raise HTTPException(status_code=404, detail="书籍或章节不存在。") from exc
    except KeyError as exc:
        raise HTTPException(status_code=404, detail="当前书籍中不存在该批注、书签或段落。") from exc
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc


def require_location(location: ReaderLocation | None) -> ReaderLocation:
    if location is None:
        raise HTTPException(status_code=409, detail="原文位置已经失效，无法定位。")
    return location


def create_reader_tools_router(reader: ReaderService, editor: EditorSessionService) -> APIRouter:
    router = APIRouter(
        prefix="/api/reader/books/{book_id}",
        tags=["Reading tools"],
        responses={
            404: {"model": ApiError, "description": "Book, chapter or resource not found"},
            422: {"model": ApiValidationError, "description": "Invalid request fields or source range"},
        },
    )
    notes = ReadingNotesService(reader, editor)

    @router.get("/annotations", response_model=PaginatedResponse[AnnotationListItem])
    def list_annotations(book_id: str, offset: Offset = 0, limit: Limit = 50, chapter_index: Chapter = None):
        with reader_errors():
            return notes.list_annotations(book_id, offset=offset, limit=limit, chapter_index=chapter_index)

    @router.post("/annotations", status_code=201, response_model=AnnotationMutationResponse)
    def create_annotation(book_id: str, payload: AnnotationCreateInput):
        with reader_errors():
            reader.get_book(book_id)
            annotation, state = editor.add_annotation(AnnotationCreateRequest(book_id=book_id, **payload.model_dump()))
            return AnnotationMutationResponse(annotation=annotation, state=state)

    @router.patch("/annotations/{annotation_id}", response_model=AnnotationMutationResponse)
    def update_annotation(book_id: str, annotation_id: str, payload: AnnotationUpdateInput):
        with reader_errors():
            reader.get_book(book_id)
            annotation, state = editor.update_annotation(book_id, annotation_id, payload)
            return AnnotationMutationResponse(annotation=annotation, state=state)

    @router.delete("/annotations/{annotation_id}", response_model=EditorState)
    def delete_annotation(book_id: str, annotation_id: str):
        with reader_errors():
            reader.get_book(book_id)
            return editor.delete_annotation(annotation_id, book_id=book_id)

    @router.get("/annotations/{annotation_id}/location", response_model=ReaderLocation, responses={409: {"model": ApiError}})
    def annotation_location(book_id: str, annotation_id: str):
        with reader_errors():
            return require_location(notes.annotation_location(book_id, annotation_id))

    @router.get("/bookmarks", response_model=PaginatedResponse[BookmarkListItem])
    def list_bookmarks(book_id: str, offset: Offset = 0, limit: Limit = 50, chapter_index: Chapter = None):
        with reader_errors():
            return notes.list_bookmarks(book_id, offset=offset, limit=limit, chapter_index=chapter_index)

    @router.post("/bookmarks", status_code=201, response_model=BookmarkMutationResponse)
    def create_bookmark(book_id: str, payload: BookmarkCreateInput):
        with reader_errors():
            reader.get_book(book_id)
            bookmark, state = editor.add_bookmark(book_id, payload)
            return BookmarkMutationResponse(bookmark=bookmark, state=state)

    @router.delete("/bookmarks/{bookmark_id}", response_model=EditorState)
    def delete_bookmark(book_id: str, bookmark_id: str):
        with reader_errors():
            reader.get_book(book_id)
            return editor.delete_bookmark(book_id, bookmark_id)

    @router.get("/bookmarks/{bookmark_id}/location", response_model=ReaderLocation, responses={409: {"model": ApiError}})
    def bookmark_location(book_id: str, bookmark_id: str):
        with reader_errors():
            return require_location(notes.bookmark_location(book_id, bookmark_id))

    @router.get("/search", response_model=PaginatedResponse[SearchResultItem])
    def search_book(
        book_id: str,
        q: Annotated[str, Query(min_length=1, max_length=300, pattern=r"\S")],
        chapter_index: Chapter = None,
        offset: Offset = 0,
        limit: Limit = 50,
    ):
        """Literal, case-insensitive search; one result per paragraph in reading order."""
        with reader_errors():
            return reader.search_page(book_id, q.strip(), chapter_index=chapter_index, offset=offset, limit=limit)

    @router.get("/paragraphs/{edit_id}/location", response_model=ReaderLocation, responses={409: {"model": ApiError}})
    def paragraph_location(book_id: str, edit_id: str):
        with reader_errors():
            reader.get_book(book_id)
            return require_location(reader.locate_edit_id(book_id, edit_id))

    @router.get(
        "/notes/export",
        response_class=Response,
        responses={200: {"model": NotesExport, "description": "Download all current annotations and bookmarks", "content": {
            "text/markdown": {"schema": {"type": "string"}},
            "application/json": {"schema": {"$ref": "#/components/schemas/NotesExport"}},
        }}},
    )
    def export_notes(book_id: str, format: Literal["markdown", "json"] = "markdown"):
        with reader_errors():
            snapshot = notes.export_snapshot(book_id)
            if format == "json":
                content = snapshot.model_dump_json(indent=2)
                extension, media_type = "json", "application/json"
            else:
                content = notes.render_markdown(snapshot)
                extension, media_type = "md", "text/markdown"
            filename = quote(f"{snapshot.book.title}-笔记.{extension}", safe="")
            return Response(content, media_type=media_type, headers={
                "Content-Disposition": f"attachment; filename=\"reading-notes.{extension}\"; filename*=UTF-8''{filename}",
                "Cache-Control": "no-store",
            })

    return router
