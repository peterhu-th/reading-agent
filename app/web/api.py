import asyncio
import json
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
from app.models.schemas import AnswerWithCitations, ChatRequest
from app.services.aiclient2api import is_api_healthy, is_provider_ready
from app.services.reading_assistant import ReadingAssistantService


ROOT = Path(__file__).resolve().parents[2]
FRONTEND_DIST = ROOT / "frontend" / "dist"
service = ReadingAssistantService()
app = FastAPI(title="Reading Memory Agent", version="0.2.0")
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
        "answer_api_healthy": is_api_healthy(),
        "answer_provider_ready": is_provider_ready(),
        "chunk_index_exists": Path(settings.VECTOR_DB_PATH).exists(),
        "chunks_exist": Path(settings.CHUNKS_JSONL_PATH).exists(),
        "embedding_model_exists": Path(settings.EMBEDDING_MODEL).exists(),
        "reranker_model_exists": Path(settings.RERANK_MODEL).exists(),
    }


@app.get("/api/books")
def books() -> list[dict]:
    return [item.model_dump(mode="json") for item in service.list_books()]


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
                )
            )
            while not prepare_task.done():
                if await request.is_disconnected():
                    cancelled.set()
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
    message = str(exc).strip()
    if not message:
        return "处理请求时发生未知错误。"
    return message[:300]


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
