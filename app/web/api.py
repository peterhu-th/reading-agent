import asyncio
import json
import logging
from contextlib import asynccontextmanager
from pathlib import Path
from threading import Event

from fastapi import FastAPI, HTTPException, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, JSONResponse, StreamingResponse
from fastapi.staticfiles import StaticFiles

from app.agent.answer_generator import stream_answer
from app.agent.citation_builder import build_citations
from app.config import get_settings
from app.memory.session_store import SessionBusyError, SessionNotFoundError
from app.models.schemas import AnnotationCreateRequest, AnswerWithCitations, ChapterRenameRequest, ChatRequest, ReaderOperationRequest
from app.services.aiclient2api import is_api_healthy, is_provider_ready
from app.services.database_update import DatabaseUpdateBusyError, DatabaseUpdateService
from app.services.editor_session import EditorSessionService
from app.services.epub_editor import EpubEditError
from app.services.reader import ReaderNotFoundError, ReaderService
from app.services.reading_assistant import ReadingAssistantService


ROOT = Path(__file__).resolve().parents[2]
FRONTEND_DIST = ROOT / "frontend" / "dist"
LOGGER = logging.getLogger(__name__)
service = ReadingAssistantService()
reader = ReaderService()
editor = EditorSessionService(reader)
database_update = DatabaseUpdateService(ROOT)


@asynccontextmanager
async def lifespan(_app: FastAPI):
    reader.reload()
    editor.reset()
    reader.books
    yield


app = FastAPI(title="Reading Memory Agent", version="0.2.0", lifespan=lifespan)
app.add_middleware(
    CORSMiddleware,
    allow_origins=["http://127.0.0.1:5173", "http://localhost:5173"],
    allow_credentials=False,
    allow_methods=["*"] ,
    allow_headers=["*"],
)


@app.get("/api/health")
def health() -> dict:
    settings = get_settings()
    return {
        "status": "ok",
        "web_api_version": 4,
        "editor_capabilities": ["direct_edit", "annotations", "chapter_delete", "chapter_rename"],
        "answer_api_healthy": is_api_healthy(),
        "answer_provider_ready": is_provider_ready(),
        "chunk_index_exists": Path(settings.VECTOR_DB_PATH).exists(),
        "chunks_exist": Path(settings.CHUNKS_JSONL_PATH).exists(),
        "embedding_model_exists": Path(settings.EMBEDDING_MODEL).exists(),
        "reranker_model_exists": Path(settings.RERANK_MODEL).exists(),
    }


@app.get("/api/books")
def books() -> list[dict]:
    return [item.model_dump(mode="json") for item in reader.books]


@app.get("/api/reader/books")
def reader_books() -> list[dict]:
    return [item.model_dump(mode="json") for item in reader.books]


@app.get("/api/reader/books/{book_id}")
def reader_book(book_id: str) -> dict:
    try:
        return reader.get_book(book_id).model_dump(mode="json")
    except ReaderNotFoundError as exc:
        raise HTTPException(status_code=404, detail="书籍不存在。") from exc


@app.get("/api/reader/books/{book_id}/chapters")
def reader_chapters(book_id: str) -> list[dict]:
    try:
        return [item.model_dump(mode="json") for item in reader.list_chapters(book_id)]
    except ReaderNotFoundError as exc:
        raise HTTPException(status_code=404, detail="书籍不存在。") from exc


@app.get("/api/reader/books/{book_id}/chapters/{chapter_index}")
def reader_chapter(book_id: str, chapter_index: int, offset: int = 0, limit: int = 80, focus_paragraph: int | None = None) -> dict:
    if offset < 0 or limit < 1 or limit > 200:
        raise HTTPException(status_code=422, detail="分页参数无效。")
    try:
        return reader.get_chapter(book_id, chapter_index, offset, limit, focus_paragraph).model_dump(mode="json")
    except ReaderNotFoundError as exc:
        raise HTTPException(status_code=404, detail="书籍或章节不存在。") from exc


@app.get("/api/reader/books/{book_id}/search")
def reader_search(book_id: str, q: str, chapter_index: int | None = None, limit: int = 30) -> list[dict]:
    if limit < 1 or limit > 100:
        raise HTTPException(status_code=422, detail="搜索数量无效。")
    try:
        return [item.model_dump(mode="json") for item in reader.search(book_id, q, chapter_index, limit)]
    except ReaderNotFoundError as exc:
        raise HTTPException(status_code=404, detail="书籍不存在。") from exc


@app.get("/api/editor/state")
def editor_state() -> dict:
    state = editor.state().model_dump(mode="json")
    update = database_update.state()
    state.update(
        {
            "database_updating": update["running"],
            "database_status": update["status"],
            "database_message": update["message"],
        }
    )
    return state


@app.post("/api/editor/operations")
def editor_operation(payload: ReaderOperationRequest) -> dict:
    try:
        return editor.apply_operation(payload).model_dump(mode="json")
    except KeyError as exc:
        raise HTTPException(status_code=404, detail="编辑段落不存在，请刷新阅读数据后重试。") from exc


@app.post("/api/editor/annotations", status_code=201)
def create_annotation(payload: AnnotationCreateRequest) -> dict:
    try:
        annotation, state = editor.add_annotation(payload)
        return {
            "annotation": annotation.model_dump(mode="json"),
            "state": state.model_dump(mode="json"),
        }
    except KeyError as exc:
        raise HTTPException(status_code=404, detail="批注选区已经失效，请重新选择。") from exc


@app.delete("/api/editor/annotations/{annotation_id}")
def delete_annotation(annotation_id: str) -> dict:
    try:
        return editor.delete_annotation(annotation_id).model_dump(mode="json")
    except KeyError as exc:
        raise HTTPException(status_code=404, detail="批注不存在。") from exc


@app.delete("/api/editor/books/{book_id}/chapters/{chapter_index}")
def delete_editor_chapter(book_id: str, chapter_index: int) -> dict:
    try:
        return editor.delete_chapter(book_id, chapter_index).model_dump(mode="json")
    except KeyError as exc:
        raise HTTPException(status_code=404, detail="章节不存在或已经删除。") from exc


@app.post("/api/editor/books/{book_id}/chapters/{chapter_index}/delete")
def delete_editor_chapter_action(book_id: str, chapter_index: int) -> dict:
    """POST action alias for clients or local proxies that reject DELETE."""

    return delete_editor_chapter(book_id, chapter_index)


@app.post("/api/editor/books/{book_id}/chapters/{chapter_index}/rename")
def rename_editor_chapter(book_id: str, chapter_index: int, payload: ChapterRenameRequest) -> dict:
    try:
        return editor.rename_chapter(book_id, chapter_index, payload.title).model_dump(mode="json")
    except KeyError as exc:
        raise HTTPException(status_code=404, detail="章节不存在或已经删除。") from exc
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc


@app.post("/api/editor/undo")
def undo_editor_operation() -> dict:
    return editor.undo().model_dump(mode="json")


@app.post("/api/editor/save")
def save_editor() -> dict:
    try:
        return editor.save().model_dump(mode="json")
    except EpubEditError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc


@app.post("/api/database/update", status_code=202)
def start_database_update() -> dict:
    try:
        return database_update.start(editor.has_unsaved_changes)
    except DatabaseUpdateBusyError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc


@app.get("/api/database/update")
def database_update_state() -> dict:
    return database_update.state()


@app.get("/api/sessions")
def list_sessions() -> list[dict]:
    return [session.model_dump(mode="json") for session in service.store.list()]


@app.post("/api/sessions", status_code=201)
def create_session() -> dict:
    return service.create_session().model_dump(mode="json")


@app.get("/api/sessions/{session_id}")
def get_session(session_id: str) -> dict:
    try:
        return service.store.get(session_id).model_dump(mode="json")
    except SessionNotFoundError as exc:
        raise HTTPException(status_code=404, detail="会话不存在或后端已经重启。") from exc


@app.delete("/api/sessions/{session_id}", status_code=204)
def delete_session(session_id: str) -> None:
    try:
        service.store.delete(session_id)
    except SessionNotFoundError as exc:
        raise HTTPException(status_code=404, detail="会话不存在。") from exc


@app.get("/api/sources/{source_id}")
def get_source(source_id: str) -> dict:
    chunk = service.get_source(source_id)
    if chunk is None:
        raise HTTPException(status_code=404, detail="引用原文不存在。")
    return {
        "source_id": source_id,
        "title": chunk.title,
        "author": chunk.author,
        "book_type": chunk.book_type,
        "chapter_index": chunk.chapter_index,
        "chapter_title": chunk.chapter_title,
        "paragraph_range": f"{chunk.start_paragraph_index}-{chunk.end_paragraph_index}",
        "text": chunk.text,
    }


@app.post("/api/chat/stream")
async def chat_stream(payload: ChatRequest, request: Request) -> StreamingResponse:
    try:
        service.store.acquire(payload.session_id)
    except SessionNotFoundError as exc:
        raise HTTPException(status_code=404, detail="会话不存在或后端已经重启。") from exc
    except SessionBusyError as exc:
        raise HTTPException(status_code=409, detail="当前会话正在生成回答，请等待或停止上一请求。") from exc

    async def generate_events():
        loop = asyncio.get_running_loop()
        status_queue: asyncio.Queue[dict] = asyncio.Queue()
        cancelled = Event()

        def on_status(stage: str, round_index: int, message: str) -> None:
            if cancelled.is_set():
                raise RuntimeError("request_cancelled")
            loop.call_soon_threadsafe(
                status_queue.put_nowait,
                {"stage": stage, "round": round_index, "message": message},
            )

        try:
            yield sse("status", {"stage": "understanding", "round": 0, "message": "正在理解问题"})
            prepare_task = asyncio.create_task(
                asyncio.to_thread(
                    service.prepare_turn,
                    payload.session_id,
                    payload.question,
                    payload.selected_books,
                    payload.debug,
                    on_status,
                    payload.scope,
                    payload.book_id,
                    payload.book_title,
                    payload.chapter_index,
                    payload.selected_text,
                    payload.selected_paragraphs,
                )
            )
            while not prepare_task.done():
                if await request.is_disconnected():
                    cancelled.set()
                    try:
                        await prepare_task
                    except Exception:
                        pass
                    return
                try:
                    event = await asyncio.wait_for(status_queue.get(), timeout=0.15)
                    yield sse("status", event)
                except TimeoutError:
                    continue
            while not status_queue.empty():
                yield sse("status", status_queue.get_nowait())
            prepared = await prepare_task

            if prepared.resolved.clarification_needed:
                message = prepared.resolved.clarification_message or "请补充书名或人物名称。"
                yield sse("clarification", {"message": message})
                yield sse("complete", {"session": prepared.session.model_dump(mode="json"), "clarification": True})
                return
            if prepared.retrieval is None:
                raise RuntimeError("检索未返回结果。")

            yield sse("status", {"stage": "answering", "round": len(prepared.retrieval.rounds) - 1, "message": "正在组织回答"})
            answer_parts: list[str] = []
            async for delta in stream_answer(
                payload.question,
                prepared.retrieval.chunks,
                prepared.retrieval.intent,
                prepared.session,
            ):
                if await request.is_disconnected():
                    cancelled.set()
                    return
                answer_parts.append(delta)
                yield sse("answer_delta", {"text": delta})

            citations = build_citations(prepared.retrieval.chunks)
            citations = [
                citation.model_copy(
                    update={
                        "reader_location": reader.locate(item.chunk.book_id, item.chunk.text)
                        or citation.reader_location
                    }
                )
                for citation, item in zip(citations, prepared.retrieval.chunks, strict=True)
            ]
            answer = AnswerWithCitations(answer="".join(answer_parts).strip(), citations=citations)
            updated = await asyncio.to_thread(service.finalize_turn, prepared, answer)
            yield sse("citations", {"items": [item.model_dump(mode="json") for item in citations]})
            trace = {
                "rounds": [item.model_dump(mode="json") for item in prepared.retrieval.rounds],
                "evidence_sufficient": prepared.retrieval.assessment.sufficient,
                "missing_aspects": prepared.retrieval.assessment.missing_aspects,
                "debug_lines": prepared.retrieval.debug.lines if payload.debug else [],
            }
            yield sse("complete", {"session": updated.model_dump(mode="json"), "trace": trace})
        except Exception as exc:
            if str(exc) != "request_cancelled":
                LOGGER.exception("Chat turn failed")
                yield sse("error", {"code": "turn_failed", "message": safe_error_message(exc)})
        finally:
            cancelled.set()
            service.store.release(payload.session_id)

    return StreamingResponse(
        generate_events(),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )


def sse(event: str, data: dict) -> str:
    return f"event: {event}\ndata: {json.dumps(data, ensure_ascii=False)}\n\n"


def safe_error_message(exc: Exception) -> str:
    message = str(exc).strip().lower()
    if any(token in message for token in ("connection", "connect", "timeout", "timed out", "502", "503")):
        return "模型服务暂时不可用，请检查模型节点后重试。"
    if any(token in message for token in ("model", "provider", "openai", "api")):
        return "模型配置或节点状态异常，请检查后端配置。"
    if any(token in message for token in ("index", "chroma", "embedding", "rerank")):
        return "本地检索资源不可用，请检查索引和本地模型。"
    return "本轮处理失败，请查看后端日志了解原因。"


if FRONTEND_DIST.exists():
    assets = FRONTEND_DIST / "assets"
    if assets.exists():
        app.mount("/assets", StaticFiles(directory=assets), name="frontend-assets")

    @app.get("/{path:path}", include_in_schema=False)
    def frontend(path: str):
        candidate = (FRONTEND_DIST / path).resolve()
        if path and candidate.is_file() and FRONTEND_DIST.resolve() in candidate.parents:
            return FileResponse(candidate)
        return FileResponse(FRONTEND_DIST / "index.html")
else:
    @app.get("/", include_in_schema=False)
    def no_frontend() -> JSONResponse:
        return JSONResponse({"message": "前端尚未构建，请在 frontend 目录运行 npm.cmd run build。"})
