import asyncio
import json
import logging
import re
import threading
import time
from contextlib import suppress
from dataclasses import dataclass, field

from fastapi import HTTPException
from sqlalchemy import delete, func, select
from starlette.concurrency import run_in_threadpool

from app.context import fit_local_context
from app.db import Conversation, Message
from app.generation import citation_check
from app.schemas import ChatResponse, Citation

logger = logging.getLogger(__name__)


@dataclass
class ChatTurn:
    conversation_id: str
    question: str
    query: str
    history: list[dict]
    citations: list[Citation]
    started: float = field(default_factory=time.perf_counter)


def followup_query(question: str, history: list[dict]) -> str:
    previous = next((m["content"] for m in reversed(history) if m["role"] == "user"), None)
    # Deterministic context expansion, visible in the response for debugging and evaluation.
    if previous and (
        re.search(r"\b(it|its|they|them|those|that|this|their)\b", question, re.I)
        or re.match(
            r"^(and\b|also\b|what about\b|how about\b|what else\b|tell me more\b|explain more\b|continue\b|elaborate\b|why\??$)",
            question,
            re.I,
        )
    ):
        return f"{previous}\nFollow-up: {question}"
    return question


class ChatService:
    def __init__(self, settings, sessions, retriever, generator):
        self.settings, self.sessions = settings, sessions
        self.retriever, self.generator = retriever, generator
        self._active: set[str] = set()
        self._lock = threading.Lock()

    def prepare(self, request) -> ChatTurn:
        started = time.perf_counter()
        # Conversation lookup/creation and active registration share the deletion lock.
        with self._lock:
            with self.sessions.begin() as session:
                if request.conversation_id:
                    conversation = session.get(Conversation, str(request.conversation_id))
                    if not conversation:
                        raise HTTPException(404, "Conversation not found")
                else:
                    conversation = Conversation(title=request.query[:120])
                    session.add(conversation)
                    session.flush()
                conversation_id = conversation.id
            if conversation_id in self._active:
                raise HTTPException(409, "A response is already running in this conversation")
            self._active.add(conversation_id)
        try:
            with self.sessions() as session:
                messages = (
                    list(
                        session.scalars(
                            select(Message)
                            .where(Message.conversation_id == conversation_id)
                            .order_by(Message.sequence.desc())
                            .limit(self.settings.history_turns * 2)
                        )
                    )
                    if self.settings.history_turns
                    else []
                )
                history = [
                    {"role": m.role, "content": m.content[:4000]} for m in reversed(messages)
                ]
            query = followup_query(request.query, history)
            hits = self.retriever.search(
                query, request.top_k, [str(value) for value in request.document_ids], request.mode
            )
            sources = []
            remaining = self.settings.context_chars
            for hit in hits:
                if remaining < 100:
                    break
                data = hit.model_dump()
                data["text"] = data["text"][:remaining]
                sources.append(Citation(**data, label=len(sources) + 1))
                remaining -= len(data["text"])
            if self.settings.llm_provider == "ollama":
                try:
                    history, sources = fit_local_context(
                        request.query, history, sources, self.settings
                    )
                except ValueError as exc:
                    raise HTTPException(422, str(exc)) from exc
            return ChatTurn(
                conversation_id, request.query, query, history, sources, started=started
            )
        except BaseException:
            self.release(conversation_id)
            raise

    def release(self, conversation_id):
        with self._lock:
            self._active.discard(conversation_id)

    def is_active(self, conversation_id):
        with self._lock:
            return conversation_id in self._active

    def delete_conversation(self, conversation_id):
        with self._lock, self.sessions.begin() as session:
            if conversation_id in self._active:
                raise HTTPException(409, "Wait for the active response to finish")
            result = session.execute(delete(Conversation).where(Conversation.id == conversation_id))
            if not result.rowcount:
                raise HTTPException(404, "Conversation not found")

    def finish(self, turn: ChatTurn, answer: str) -> ChatResponse:
        if not answer.strip():
            raise RuntimeError("The model returned an empty response")
        status, citations = citation_check(answer, turn.citations)
        result = ChatResponse(
            conversation_id=turn.conversation_id,
            answer=answer,
            citations=citations,
            grounded=status == "valid",
            citation_status=status,
            provider=self.settings.llm_provider,
            retrieval_query=turn.query,
            latency_ms=round((time.perf_counter() - turn.started) * 1000, 2),
        )
        # Persist only a complete user/assistant pair; disconnects and failures leave no half-turn.
        with self.sessions.begin() as session:
            sequence = (
                session.scalar(
                    select(func.max(Message.sequence)).where(
                        Message.conversation_id == turn.conversation_id
                    )
                )
                or 0
            )
            session.add_all(
                [
                    Message(
                        conversation_id=turn.conversation_id,
                        sequence=sequence + 1,
                        role="user",
                        content=turn.question,
                    ),
                    Message(
                        conversation_id=turn.conversation_id,
                        sequence=sequence + 2,
                        role="assistant",
                        content=answer,
                        citations=[c.model_dump() for c in citations],
                    ),
                ]
            )
        return result

    async def answer(self, turn: ChatTurn):
        try:
            parts = [
                part
                async for part in self.generator.stream(turn.question, turn.citations, turn.history)
            ]
            return await run_in_threadpool(self.finish, turn, "".join(parts))
        finally:
            self.release(turn.conversation_id)

    async def events(self, turn: ChatTurn):
        def event(name, data):
            return f"event: {name}\ndata: {json.dumps(data, ensure_ascii=False)}\n\n"

        stream = self.generator.stream(turn.question, turn.citations, turn.history)
        pending = None
        try:
            yield event(
                "sources",
                {
                    "conversation_id": turn.conversation_id,
                    "citations": [c.model_dump() for c in turn.citations],
                    "retrieval_query": turn.query,
                },
            )
            parts = []
            pending = asyncio.create_task(anext(stream))
            while True:
                ready, _ = await asyncio.wait(
                    {pending}, timeout=self.settings.stream_heartbeat_seconds
                )
                if not ready:
                    yield ": keep-alive\n\n"
                    continue
                try:
                    part = pending.result()
                except StopAsyncIteration:
                    break
                parts.append(part)
                yield event("delta", {"text": part})
                pending = asyncio.create_task(anext(stream))
            result = await run_in_threadpool(self.finish, turn, "".join(parts))
            yield event("done", result.model_dump())
        except Exception:
            logger.exception("Chat generation failed in conversation %s", turn.conversation_id)
            yield event(
                "error",
                {
                    "message": "Response generation failed. Please retry. No partial answer was saved."
                },
            )
        finally:
            try:
                if pending is not None and not pending.done():
                    pending.cancel()
                    with suppress(asyncio.CancelledError):
                        await pending
            finally:
                try:
                    await stream.aclose()
                finally:
                    self.release(turn.conversation_id)
