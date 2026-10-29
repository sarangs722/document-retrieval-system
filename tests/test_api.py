import json
import time
from uuid import uuid4

from fastapi.testclient import TestClient
from pydantic import SecretStr
from sqlalchemy import select

from app.db import Chunk, Document, Message
from app.main import create_app


def test_health_ui_and_schema(client):
    assert client.get("/api/health").json() == {"status": "ok"}
    assert client.get("/").status_code == 200
    assert "Follow the source" in client.get("/").text
    assert client.get("/static/app.js").status_code == 200
    assert "/api/chat/stream" in client.get("/openapi.json").json()["paths"]
    assert client.get("/api/config").json()["demo_mode"] is True


def test_upload_ingest_search_and_delete(client, ingest):
    doc = ingest()
    hits = client.post("/api/search", json={"query": "PDF ingestion"}).json()
    assert hits and hits[0]["document_id"] == doc["id"]
    assert hits[0]["page"] == 1
    assert "PDF parsing" in hits[0]["text"]
    assert client.get(f"/api/documents/{doc['id']}/chunks").json()[0]["ordinal"] == 0
    assert client.delete(f"/api/documents/{doc['id']}").status_code == 204
    assert client.post("/api/search", json={"query": "PDF ingestion"}).json() == []
    assert client.app.state.vectors.collection.count() == 0
    with client.app.state.sessions() as session:
        assert session.scalar(select(Chunk)) is None
    assert not list((client.app.state.settings.data_dir / "uploads").glob("*"))


def test_deduplicate_and_sanitize_filename(client, ingest):
    doc = ingest("manual.txt", "A unique document about indexing.")
    duplicate = client.post(
        "/api/documents",
        files={"file": ("../../another.txt", b"A unique document about indexing.")},
    )
    assert duplicate.json()["id"] == doc["id"]
    other = client.post("/api/documents", files={"file": ("..\\..\\other.txt", b"Different data")})
    assert other.json()["filename"] == "other.txt"
    assert len(client.get("/api/documents").json()) == 2


def test_document_filter_and_modes(client, ingest):
    a = ingest(
        "a.txt", "Solar panels convert sunlight into electricity. Photovoltaic power is renewable."
    )
    b = ingest("b.txt", "Database replication maintains redundant copies of data for availability.")
    for mode in ("keyword", "vector", "hybrid"):
        hits = client.post("/api/search", json={"query": "solar electricity", "mode": mode}).json()
        assert hits and hits[0]["document_id"] == a["id"]
        filtered = client.post(
            "/api/search",
            json={"query": "solar electricity", "document_ids": [b["id"]], "mode": mode},
        ).json()
        assert filtered == []
    assert (
        client.post("/api/search", json={"query": "solar", "document_ids": [str(uuid4())]}).json()
        == []
    )


def test_queued_and_stale_indexes_are_invisible(client, ingest):
    doc = ingest()
    with client.app.state.sessions.begin() as session:
        session.get(Document, doc["id"]).index_signature = "different-model"
    assert client.post("/api/search", json={"query": "ingestion"}).json() == []
    assert client.post(f"/api/documents/{doc['id']}/reindex").json()["status"] == "queued"
    assert client.post("/api/search", json={"query": "ingestion"}).json() == []
    client.app.state.worker.process(doc["id"])
    assert client.post("/api/search", json={"query": "ingestion"}).json()


def test_failed_ingestion_and_retry(client):
    response = client.post("/api/documents", files={"file": ("bad.txt", b"\xff\xfe\x00")})
    doc = response.json()
    client.app.state.worker.process(doc["id"])
    failed = client.get(f"/api/documents/{doc['id']}").json()
    assert failed["status"] == "failed" and "UTF-8" in failed["error"]
    assert client.get("/api/stats").json()["chunks"] == 0
    assert client.post(f"/api/documents/{doc['id']}/reindex").status_code == 202


def test_upload_validation(client):
    for filename, content, code in [
        ("empty.txt", b"", 422),
        ("run.exe", b"data", 415),
        ("fake.pdf", b"not a pdf", 422),
    ]:
        assert (
            client.post("/api/documents", files={"file": (filename, content)}).status_code == code
        )
    assert not list((client.app.state.settings.data_dir / "uploads").glob("*"))
    assert (
        client.post(
            "/api/documents", content=b"x", headers={"content-length": str(30 * 1024 * 1024)}
        ).status_code
        == 413
    )
    for body in (
        {"query": "   "},
        {"query": "a", "top_k": 0},
        {"query": "a", "document_ids": ["bad"]},
    ):
        assert client.post("/api/search", json=body).status_code == 422
    assert client.get("/api/documents/not-a-uuid").status_code == 422
    assert client.get(f"/api/documents/{uuid4()}").status_code == 404


def test_chat_memory_and_citations(client, ingest):
    ingest()
    result = client.post("/api/chat", json={"query": "How does document ingestion work?"})
    assert result.status_code == 200, result.text
    answer = result.json()
    assert answer["grounded"] is True and answer["citation_status"] == "valid"
    assert answer["citations"][0]["filename"] == "manual.txt"
    assert "[1]" in answer["answer"]
    followup = client.post(
        "/api/chat", json={"query": "And citations?", "conversation_id": answer["conversation_id"]}
    ).json()
    assert "document ingestion" in followup["retrieval_query"]
    history = client.get(f"/api/conversations/{answer['conversation_id']}").json()
    assert [m["role"] for m in history["messages"]] == ["user", "assistant", "user", "assistant"]
    assert client.delete(f"/api/conversations/{answer['conversation_id']}").status_code == 204
    with client.app.state.sessions() as session:
        assert session.scalar(select(Message)) is None


def test_conversation_serialization_and_active_deletion(client, ingest):
    from fastapi import HTTPException

    from app.schemas import ChatRequest

    ingest()
    service = client.app.state.chat
    turn = service.prepare(ChatRequest(query="Explain ingestion"))
    try:
        response = client.post(
            "/api/chat", json={"query": "Another question", "conversation_id": turn.conversation_id}
        )
        assert response.status_code == 409
        assert client.delete(f"/api/conversations/{turn.conversation_id}").status_code == 409
        try:
            service.prepare(ChatRequest(query="Again", conversation_id=turn.conversation_id))
            raise AssertionError("Concurrent preparation should be rejected")
        except HTTPException as exc:
            assert exc.status_code == 409
    finally:
        service.release(turn.conversation_id)
    assert client.delete(f"/api/conversations/{turn.conversation_id}").status_code == 204


def test_streaming_event_contract(client, ingest):
    ingest()
    response = client.post("/api/chat/stream", json={"query": "Explain PDF parsing"})
    assert response.headers["content-type"].startswith("text/event-stream")
    events = []
    for block in response.text.strip().split("\n\n"):
        event, data = block.split("\n", 1)
        events.append((event.removeprefix("event: "), json.loads(data.removeprefix("data: "))))
    assert events[0][0] == "sources" and events[-1][0] == "done"
    assert any(kind == "delta" for kind, _ in events)
    assert (
        "".join(data["text"] for kind, data in events if kind == "delta") == events[-1][1]["answer"]
    )


def test_abstain_on_empty_or_unrelated_corpus(client, ingest):
    for _ in range(2):
        response = client.post(
            "/api/chat", json={"query": "quantum gravitational wormholes"}
        ).json()
        assert response["grounded"] is False and response["citations"] == []
        assert "couldn't find" in response["answer"]
        ingest()


def test_generation_failure_does_not_persist_half_turn(client, ingest, monkeypatch):
    ingest()

    async def fail(*args):
        yield "partial text"
        raise RuntimeError("private provider details")

    monkeypatch.setattr(client.app.state.chat.generator, "stream", fail)
    response = client.post("/api/chat/stream", json={"query": "PDF parsing"})
    assert "event: error" in response.text
    assert "private provider details" not in response.text
    with client.app.state.sessions() as session:
        assert session.scalar(select(Message)) is None
    assert not client.app.state.chat._active
    assert client.post("/api/chat", json={"query": "PDF parsing"}).status_code == 502


def test_vector_failure_cleans_up_and_remains_hidden(client, monkeypatch):
    doc = client.post(
        "/api/documents", files={"file": ("file.txt", b"Vector indexing failure test")}
    ).json()

    def fail(*args):
        raise RuntimeError("vector store unavailable")

    monkeypatch.setattr(client.app.state.vectors, "upsert", fail)
    client.app.state.worker.process(doc["id"])
    assert client.get(f"/api/documents/{doc['id']}").json()["status"] == "failed"
    assert client.post("/api/search", json={"query": "indexing"}).json() == []


def test_auth_applies_to_every_data_endpoint(settings):
    settings.api_key = SecretStr("test-secret")
    with TestClient(create_app(settings)) as client:
        for method, path in [
            ("GET", "/api/config"),
            ("GET", "/api/documents"),
            ("GET", "/api/stats"),
            ("GET", "/api/conversations"),
            ("POST", "/api/search"),
            ("POST", "/api/chat/stream"),
        ]:
            assert client.request(method, path, json={"query": "question"}).status_code == 401
        assert client.get("/api/health").status_code == 200
        assert client.get("/api/config", headers={"X-API-Key": "test-secret"}).status_code == 200


def test_recovery_after_restart(settings):
    settings.worker_enabled = False
    with TestClient(create_app(settings)) as client:
        item = client.post(
            "/api/documents",
            files={"file": ("recovery.txt", b"Durable ingestion queues recover processing jobs.")},
        ).json()
        with client.app.state.sessions.begin() as session:
            session.get(Document, item["id"]).status = "processing"
    settings.worker_enabled = True
    with TestClient(create_app(settings)) as client:
        deadline = time.monotonic() + 10
        while time.monotonic() < deadline:
            document = client.get(f"/api/documents/{item['id']}").json()
            if document["status"] in ("ready", "failed"):
                break
            time.sleep(0.05)
        assert document["status"] == "ready", document
        assert client.post("/api/search", json={"query": "durable ingestion"}).json()


def test_stream_cancellation_discards_partial_turn(client, ingest):
    import asyncio

    from app.schemas import ChatRequest

    ingest()
    service = client.app.state.chat
    turn = service.prepare(ChatRequest(query="Explain ingestion"))

    async def cancel_stream():
        events = service.events(turn)
        assert "event: sources" in await anext(events)
        assert "event: delta" in await anext(events)
        await events.aclose()

    asyncio.run(cancel_stream())
    assert not service.is_active(turn.conversation_id)
    with client.app.state.sessions() as session:
        assert session.scalar(select(Message)) is None


def test_answer_latency_includes_retrieval(client, ingest, monkeypatch):
    ingest()
    retriever = client.app.state.retriever
    original = retriever.search

    def delayed(*args, **kwargs):
        time.sleep(0.03)
        return original(*args, **kwargs)

    monkeypatch.setattr(retriever, "search", delayed)
    result = client.post("/api/chat", json={"query": "Explain ingestion"}).json()
    assert result["latency_ms"] >= 30
