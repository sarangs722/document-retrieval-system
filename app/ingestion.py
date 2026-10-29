import logging
import threading
from pathlib import Path

from sqlalchemy import delete, func, select, update

from app.db import Chunk, Document, new_id
from app.parsing import DocumentError, chunk_pages, parse

logger = logging.getLogger(__name__)


class IngestionWorker:
    """Durable SQL-backed queue, serviced by one worker in one application process.

    A mutation lock serializes ingestion/reindex/delete; uploads and reads stay available.
    Multi-process workers require a distributed claim/lease and are intentionally unsupported.
    """

    def __init__(self, settings, sessions, embedder, vectors):
        self.settings, self.sessions = settings, sessions
        self.embedder, self.vectors = embedder, vectors
        self.mutation_lock = threading.RLock()
        self.stop_event = threading.Event()
        self.wake_event = threading.Event()
        self.thread: threading.Thread | None = None

    def source(self, document: Document) -> Path:
        return (
            self.settings.data_dir
            / "uploads"
            / f"{document.id}{Path(document.filename).suffix.lower()}"
        )

    def start(self):
        with self.sessions.begin() as session:
            session.execute(
                update(Document).where(Document.status == "processing").values(status="queued")
            )
        # Finish a delete interrupted between vector removal and SQL commit.
        with self.sessions() as session:
            deleting = list(
                session.scalars(select(Document.id).where(Document.status == "deleting"))
            )
        for document_id in deleting:
            self.delete(document_id)
        self.thread = threading.Thread(target=self._run, name="atlas-ingestion", daemon=True)
        self.thread.start()

    def stop(self):
        self.stop_event.set()
        self.wake_event.set()
        if self.thread:
            self.thread.join(timeout=45)
            if self.thread.is_alive():
                logger.warning(
                    "Ingestion is still finishing; its processing job will recover on restart"
                )

    def _run(self):
        while not self.stop_event.is_set():
            try:
                if self.process_next():
                    continue
            except Exception:
                logger.exception("Queue polling failed; retrying on the next poll")
            self.wake_event.wait(timeout=1)
            self.wake_event.clear()

    def process_next(self) -> bool:
        with self.mutation_lock:
            with self.sessions() as session:
                document_id = session.scalar(
                    select(Document.id)
                    .where(Document.status == "queued")
                    .order_by(Document.created_at)
                    .limit(1)
                )
            if not document_id:
                return False
            self.process(document_id)
            return True

    def process(self, document_id: str):
        with self.mutation_lock:
            with self.sessions.begin() as session:
                document = session.get(Document, document_id)
                if not document or document.status != "queued":
                    return
                document.status, document.error = "processing", None
                source = self.source(document)
                suffix = Path(document.filename).suffix.lower()
            try:
                pages = parse(source, suffix, self.settings)
                pieces = chunk_pages(pages, self.settings, self.embedder)
                with self.sessions() as session:
                    existing = session.scalar(
                        select(func.count())
                        .select_from(Chunk)
                        .where(Chunk.document_id != document_id)
                    )
                if existing + len(pieces) > self.settings.max_corpus_chunks:
                    raise DocumentError(
                        "Corpus chunk limit reached. Delete documents before adding more."
                    )
                chunks = [
                    Chunk(
                        id=new_id(),
                        document_id=document_id,
                        ordinal=i,
                        page=piece.page,
                        text=piece.text,
                    )
                    for i, piece in enumerate(pieces)
                ]
                embeddings = self.embedder.encode([chunk.text for chunk in chunks])
                self.vectors.delete_document(document_id)
                self.vectors.upsert(chunks, embeddings)
                with self.sessions.begin() as session:
                    session.execute(delete(Chunk).where(Chunk.document_id == document_id))
                    session.add_all(chunks)
                    document = session.get(Document, document_id)
                    document.status = "ready"
                    document.page_count, document.chunk_count = len(pages), len(chunks)
                    document.index_signature = self.embedder.signature
                logger.info(
                    "Indexed document %s: %d pages, %d chunks", document_id, len(pages), len(chunks)
                )
            except Exception as exc:
                logger.exception("Ingestion failed for document %s", document_id)
                try:
                    self.vectors.delete_document(document_id)
                except Exception:
                    logger.exception("Vector cleanup failed; SQL status still prevents retrieval")
                with self.sessions.begin() as session:
                    session.execute(delete(Chunk).where(Chunk.document_id == document_id))
                    document = session.get(Document, document_id)
                    document.status = "failed"
                    document.chunk_count = 0
                    document.error = (
                        str(exc)
                        if isinstance(exc, DocumentError)
                        else "Indexing failed. Check server logs and retry."
                    )

    def delete(self, document_id: str) -> bool:
        with self.mutation_lock:
            with self.sessions.begin() as session:
                document = session.get(Document, document_id)
                if not document:
                    return False
                document.status = "deleting"
                source = self.source(document)
            self.vectors.delete_document(document_id)
            source.unlink(missing_ok=True)
            with self.sessions.begin() as session:
                session.execute(delete(Document).where(Document.id == document_id))
            return True

    def requeue(self, document_id: str) -> str | None:
        with self.mutation_lock, self.sessions.begin() as session:
            document = session.get(Document, document_id)
            if not document:
                return None
            if document.status in ("processing", "queued", "deleting"):
                return document.status
            document.status, document.error = "queued", None
        self.wake_event.set()
        return "queued"
