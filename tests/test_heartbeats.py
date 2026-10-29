import asyncio

from sqlalchemy import select

from app.db import Message
from app.schemas import ChatRequest


def test_heartbeat_keeps_slow_model_stream_open_and_cancel_closes_it(client, ingest, monkeypatch):
    ingest()
    closed = []

    async def slow(*args):
        try:
            await asyncio.sleep(10)
            yield "never reached"
        finally:
            closed.append(True)

    service = client.app.state.chat
    service.settings.stream_heartbeat_seconds = 0.01
    monkeypatch.setattr(service.generator, "stream", slow)
    turn = service.prepare(ChatRequest(query="Explain ingestion"))

    async def run():
        events = service.events(turn)
        assert "event: sources" in await anext(events)
        assert await anext(events) == ": keep-alive\n\n"
        await events.aclose()

    asyncio.run(run())
    assert closed == [True]
    assert not service.is_active(turn.conversation_id)
    with client.app.state.sessions() as session:
        assert session.scalar(select(Message)) is None
