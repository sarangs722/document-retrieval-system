import hashlib
import hmac
import logging
import os
import secrets
import time
from contextlib import asynccontextmanager
from pathlib import Path
from uuid import UUID, uuid4

from fastapi import Depends, FastAPI, File, HTTPException, Query, Request, UploadFile
from fastapi.openapi.docs import get_redoc_html, get_swagger_ui_html
from fastapi.responses import FileResponse, HTMLResponse, JSONResponse, StreamingResponse
from fastapi.security import APIKeyHeader
from fastapi.staticfiles import StaticFiles
from sqlalchemy import func, select, text
from sqlalchemy.exc import IntegrityError
from starlette.concurrency import run_in_threadpool

from app.chat import ChatService
from app.config import Settings
from app.db import Chunk, Conversation, Document, make_engine, new_id, session_factory
from app.embeddings import Embedder
from app.generation import Generator
from app.ingestion import IngestionWorker
from app.retrieval import Retriever, retrieval_configuration
from app.schemas import ChatRequest, ChatResponse, DocumentOut, SearchHit, SearchRequest
from app.vectors import VectorIndex

logger = logging.getLogger(__name__)
STATIC = Path(__file__).parent / "static"


class BodyLimitMiddleware:
    """Limit multipart bytes before the framework can spool an oversized upload."""

    def __init__(self, app, limit):
        self.app, self.limit = app, limit

    async def __call__(self, scope, receive, send):
        if scope["type"] != "http":
            return await self.app(scope, receive, send)
        headers = dict(scope.get("headers", []))
        try:
            size = int(headers.get(b"content-length", b"0"))
        except ValueError:
            return await JSONResponse({"detail": "Invalid Content-Length"}, 400)(
                scope, receive, send
            )
        if size > self.limit:
            return await JSONResponse({"detail": "Request body exceeds upload limit"}, 413)(
                scope, receive, send
            )
        total = 0

        async def limited_receive():
            nonlocal total
            message = await receive()
            total += len(message.get("body", b""))
            if total > self.limit:
                raise HTTPException(413, "Request body exceeds upload limit")
            return message

        await self.app(scope, limited_receive, send)


def create_app(settings: Settings | None = None) -> FastAPI:
    settings = settings or Settings()

    @asynccontextmanager
    async def lifespan(app):
        app_logger = logging.getLogger("app")
        if not app_logger.handlers:
            handler = logging.StreamHandler()
            handler.setFormatter(
                logging.Formatter("%(asctime)s %(levelname)s %(name)s %(message)s")
            )
            app_logger.addHandler(handler)
            app_logger.setLevel(logging.INFO)
            app_logger.propagate = False
        settings.data_dir.mkdir(parents=True, exist_ok=True)
        (settings.data_dir / "uploads").mkdir(exist_ok=True)
        engine = make_engine(settings.database_url)
        if settings.auto_migrate:
            from app.migrate import migrate

            await run_in_threadpool(migrate, engine)
        sessions = session_factory(engine)
        embedder = Embedder(settings)
        vectors = await run_in_threadpool(VectorIndex, settings, embedder.signature)
        worker = IngestionWorker(settings, sessions, embedder, vectors)
        generator = Generator(settings)
        app.state.settings, app.state.sessions = settings, sessions
        app.state.engine, app.state.embedder, app.state.vectors = engine, embedder, vectors
        app.state.worker = worker
        app.state.retriever = Retriever(sessions, embedder, vectors, settings)
        app.state.chat = ChatService(settings, sessions, app.state.retriever, generator)
        if settings.worker_enabled:
            await run_in_threadpool(worker.start)
        logger.info(
            "Atlas started: embeddings=%s, generation=%s",
            settings.embedding_provider,
            settings.llm_provider,
        )
        try:
            yield
        finally:
            await run_in_threadpool(worker.stop)
            await generator.close()
            await run_in_threadpool(embedder.close)
            await run_in_threadpool(vectors.close)
            engine.dispose()

    app = FastAPI(
        title="Document Atlas",
        version="0.1.0",
        description="Citation-aware RAG with durable document ingestion and hybrid retrieval.",
        lifespan=lifespan,
        docs_url=None,
        redoc_url=None,
    )
    app.add_middleware(BodyLimitMiddleware, limit=(settings.max_upload_mb + 1) * 1024 * 1024)

    @app.middleware("http")
    async def request_context(request, call_next):
        request_id = str(uuid4())
        docs_page = request.url.path in {"/docs", "/redoc"}
        if docs_page:
            request.state.csp_nonce = secrets.token_urlsafe(24)
        start = time.perf_counter()
        response = await call_next(request)
        response.headers["X-Request-ID"] = request_id
        response.headers["X-Content-Type-Options"] = "nosniff"
        response.headers["Referrer-Policy"] = "same-origin"
        if docs_page:
            # Only documentation uses the framework's CDN bundles and a nonced bootstrap.
            response.headers["Content-Security-Policy"] = (
                f"default-src 'self'; script-src 'self' https://cdn.jsdelivr.net 'nonce-{request.state.csp_nonce}'; "
                "style-src 'self' https://cdn.jsdelivr.net 'unsafe-inline'; img-src 'self' data: https://cdn.redoc.ly; "
                "connect-src 'self'; worker-src 'self' blob:; frame-ancestors 'none'; base-uri 'self'; form-action 'self'"
            )
            response.headers["Cache-Control"] = "no-store"
        else:
            response.headers["Content-Security-Policy"] = (
                "default-src 'self'; script-src 'self'; style-src 'self'; img-src 'self' data:; connect-src 'self'; frame-ancestors 'none'; base-uri 'self'; form-action 'self'"
            )
        logger.info(
            "request=%s method=%s path=%s status=%s elapsed_ms=%.1f",
            request_id,
            request.method,
            request.url.path,
            response.status_code,
            (time.perf_counter() - start) * 1000,
        )
        return response

    api_key_header = APIKeyHeader(name="X-API-Key", auto_error=False)

    def authorize(provided: str | None = Depends(api_key_header)):
        expected = settings.api_key
        if expected and not hmac.compare_digest(
            (provided or "").encode(), expected.get_secret_value().encode()
        ):
            raise HTTPException(401, "A valid X-API-Key header is required")

    auth = [Depends(authorize)]

    @app.get("/docs", include_in_schema=False)
    def swagger_docs(request: Request):
        response = get_swagger_ui_html(
            openapi_url=app.openapi_url,
            title=f"{app.title} - API reference",
            swagger_favicon_url="/static/favicon.svg",
            swagger_ui_parameters={"validatorUrl": None},
        )
        html = response.body.decode().replace(
            "<script", f'<script nonce="{request.state.csp_nonce}"'
        )
        return HTMLResponse(html)

    @app.get("/redoc", include_in_schema=False)
    def redoc_docs(request: Request):
        response = get_redoc_html(
            openapi_url=app.openapi_url,
            title=f"{app.title} - ReDoc",
            redoc_favicon_url="/static/favicon.svg",
            with_google_fonts=False,
        )
        html = response.body.decode().replace(
            "<script", f'<script nonce="{request.state.csp_nonce}"'
        )
        return HTMLResponse(html)

    @app.get("/api/health", tags=["system"])
    def health(request: Request):
        try:
            with request.app.state.sessions() as session:
                session.execute(text("SELECT 1"))
            request.app.state.vectors.client.heartbeat()
            worker = request.app.state.worker
            if settings.worker_enabled and (not worker.thread or not worker.thread.is_alive()):
                return JSONResponse(
                    {"status": "degraded", "detail": "Ingestion worker unavailable"}, 503
                )
            return {"status": "ok"}
        except Exception:
            logger.exception("Readiness check failed")
            return JSONResponse({"status": "unavailable"}, 503)

    @app.get("/api/config", dependencies=auth, tags=["system"])
    def config(request: Request):
        return {
            "embedding_provider": settings.embedding_provider,
            "embedding_model": settings.embedding_model,
            "llm_provider": settings.llm_provider,
            "llm_model": settings.llm_model if settings.llm_provider != "extractive" else None,
            "chunk_strategy": settings.chunk_strategy,
            "chunk_words": settings.chunk_words,
            "chunk_overlap_words": settings.chunk_overlap_words,
            "semantic_threshold": settings.semantic_threshold,
            "retrieval": retrieval_configuration(settings),
            "implementation_sha256": {
                name: hashlib.sha256((Path(__file__).parent / name).read_bytes()).hexdigest()
                for name in ("parsing.py", "embeddings.py", "retrieval.py")
            },
            "index_signature": request.app.state.embedder.signature,
            "max_upload_mb": settings.max_upload_mb,
            "demo_mode": settings.embedding_provider == "hash",
        }

    @app.get("/api/stats", dependencies=auth, tags=["system"])
    def stats(request: Request):
        with request.app.state.sessions() as session:
            statuses = dict(
                session.execute(
                    select(Document.status, func.count()).group_by(Document.status)
                ).all()
            )
            return {
                "documents": sum(statuses.values()),
                "statuses": statuses,
                "chunks": session.scalar(select(func.count()).select_from(Chunk)),
                "conversations": session.scalar(select(func.count()).select_from(Conversation)),
            }

    @app.post(
        "/api/documents",
        response_model=DocumentOut,
        status_code=202,
        dependencies=auth,
        tags=["documents"],
    )
    def upload(request: Request, file: UploadFile = File(...)):
        filename = (file.filename or "document").replace("\\", "/").split("/")[-1][:255]
        suffix = Path(filename).suffix.lower()
        if suffix not in {".pdf", ".txt", ".md"}:
            raise HTTPException(415, "Supported formats: PDF, UTF-8 TXT, Markdown")
        document_id = new_id()
        destination = settings.data_dir / "uploads" / f"{document_id}{suffix}"
        temporary = destination.with_suffix(".part")
        hasher, size = hashlib.sha256(), 0
        try:
            with temporary.open("xb") as target:
                while block := file.file.read(64 * 1024):
                    size += len(block)
                    if size > settings.max_upload_mb * 1024 * 1024:
                        raise HTTPException(413, "File exceeds upload limit")
                    hasher.update(block)
                    target.write(block)
            if not size:
                raise HTTPException(422, "File is empty")
            if suffix == ".pdf":
                with temporary.open("rb") as source:
                    if b"%PDF-" not in source.read(1024):
                        raise HTTPException(422, "File does not contain a valid PDF header")
            checksum = hasher.hexdigest()
            with request.app.state.sessions() as session:
                existing = session.scalar(select(Document).where(Document.sha256 == checksum))
                if existing:
                    return existing
                os.replace(temporary, destination)
                document = Document(
                    id=document_id, filename=filename, sha256=checksum, size_bytes=size
                )
                session.add(document)
                try:
                    session.commit()
                except IntegrityError:
                    session.rollback()
                    destination.unlink(missing_ok=True)
                    existing = session.scalar(select(Document).where(Document.sha256 == checksum))
                    if existing:
                        return existing
                    raise
            request.app.state.worker.wake_event.set()
            return document
        except BaseException:
            destination.unlink(missing_ok=True)
            raise
        finally:
            temporary.unlink(missing_ok=True)
            file.file.close()

    @app.get(
        "/api/documents", response_model=list[DocumentOut], dependencies=auth, tags=["documents"]
    )
    def documents(
        request: Request, limit: int = Query(100, ge=1, le=200), offset: int = Query(0, ge=0)
    ):
        with request.app.state.sessions() as session:
            return list(
                session.scalars(
                    select(Document)
                    .order_by(Document.created_at.desc())
                    .offset(offset)
                    .limit(limit)
                )
            )

    @app.get(
        "/api/documents/{document_id}",
        response_model=DocumentOut,
        dependencies=auth,
        tags=["documents"],
    )
    def document(request: Request, document_id: UUID):
        with request.app.state.sessions() as session:
            item = session.get(Document, str(document_id))
            if not item:
                raise HTTPException(404, "Document not found")
            return item

    @app.get("/api/documents/{document_id}/chunks", dependencies=auth, tags=["documents"])
    def chunks(
        request: Request,
        document_id: UUID,
        limit: int = Query(50, ge=1, le=100),
        offset: int = Query(0, ge=0),
    ):
        with request.app.state.sessions() as session:
            if not session.get(Document, str(document_id)):
                raise HTTPException(404, "Document not found")
            rows = session.scalars(
                select(Chunk)
                .where(Chunk.document_id == str(document_id))
                .order_by(Chunk.ordinal)
                .offset(offset)
                .limit(limit)
            )
            return [
                {"id": c.id, "page": c.page, "ordinal": c.ordinal, "text": c.text} for c in rows
            ]

    @app.post(
        "/api/documents/{document_id}/reindex",
        status_code=202,
        dependencies=auth,
        tags=["documents"],
    )
    def reindex(request: Request, document_id: UUID):
        result = request.app.state.worker.requeue(str(document_id))
        if result is None:
            raise HTTPException(404, "Document not found")
        if result == "deleting":
            raise HTTPException(409, "Document is being deleted")
        return {"id": str(document_id), "status": result}

    @app.delete(
        "/api/documents/{document_id}", status_code=204, dependencies=auth, tags=["documents"]
    )
    def remove_document(request: Request, document_id: UUID):
        if not request.app.state.worker.delete(str(document_id)):
            raise HTTPException(404, "Document not found")

    @app.post("/api/search", response_model=list[SearchHit], dependencies=auth, tags=["retrieval"])
    def search(request: Request, body: SearchRequest):
        return request.app.state.retriever.search(
            body.query, body.top_k, [str(x) for x in body.document_ids], body.mode
        )

    @app.post("/api/chat", response_model=ChatResponse, dependencies=auth, tags=["chat"])
    async def chat(request: Request, body: ChatRequest):
        turn = await run_in_threadpool(request.app.state.chat.prepare, body)
        try:
            return await request.app.state.chat.answer(turn)
        except Exception as exc:
            logger.exception("Response generation failed")
            raise HTTPException(502, "Response generation failed. Please retry.") from exc

    @app.post("/api/chat/stream", dependencies=auth, tags=["chat"])
    async def chat_stream(request: Request, body: ChatRequest):
        turn = await run_in_threadpool(request.app.state.chat.prepare, body)
        return StreamingResponse(
            request.app.state.chat.events(turn),
            media_type="text/event-stream",
            headers={"Cache-Control": "no-cache, no-transform", "X-Accel-Buffering": "no"},
        )

    @app.get("/api/conversations", dependencies=auth, tags=["chat"])
    def conversations(
        request: Request, limit: int = Query(50, ge=1, le=200), offset: int = Query(0, ge=0)
    ):
        with request.app.state.sessions() as session:
            rows = session.scalars(
                select(Conversation)
                .order_by(Conversation.created_at.desc())
                .offset(offset)
                .limit(limit)
            )
            return [
                {"id": row.id, "title": row.title, "created_at": row.created_at} for row in rows
            ]

    @app.get("/api/conversations/{conversation_id}", dependencies=auth, tags=["chat"])
    def conversation(request: Request, conversation_id: UUID):
        with request.app.state.sessions() as session:
            item = session.get(Conversation, str(conversation_id))
            if not item:
                raise HTTPException(404, "Conversation not found")
            return {
                "id": item.id,
                "title": item.title,
                "messages": [
                    {
                        "id": m.id,
                        "role": m.role,
                        "content": m.content,
                        "citations": m.citations,
                        "created_at": m.created_at,
                    }
                    for m in item.messages
                ],
            }

    @app.delete(
        "/api/conversations/{conversation_id}", status_code=204, dependencies=auth, tags=["chat"]
    )
    def remove_conversation(request: Request, conversation_id: UUID):
        request.app.state.chat.delete_conversation(str(conversation_id))

    app.mount("/static", StaticFiles(directory=str(STATIC)), name="static")

    @app.get("/", include_in_schema=False)
    def index():
        return FileResponse(STATIC / "index.html")

    return app


app = create_app()
