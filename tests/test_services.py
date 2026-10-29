"""Opt-in real PostgreSQL and HTTP Chroma smoke test; CI provides both services."""

import os
import time
from uuid import uuid4

import httpx
import pytest
from fastapi.testclient import TestClient

from app.main import create_app


@pytest.mark.skipif(
    not os.environ.get("ATLAS_TEST_DATABASE_URL"), reason="External services not configured"
)
def test_postgres_and_http_chroma(settings):
    settings.database_url = os.environ["ATLAS_TEST_DATABASE_URL"]
    settings.chroma_host = os.environ.get("ATLAS_TEST_CHROMA_HOST", "127.0.0.1")
    settings.chroma_port = int(os.environ.get("ATLAS_TEST_CHROMA_PORT", "8001"))
    deadline = time.monotonic() + 60
    while True:
        try:
            response = httpx.get(
                f"http://{settings.chroma_host}:{settings.chroma_port}/api/v2/heartbeat", timeout=2
            )
            response.raise_for_status()
            break
        except httpx.HTTPError:
            if time.monotonic() > deadline:
                raise
            time.sleep(0.5)
    with TestClient(create_app(settings)) as client:
        content = f"PostgreSQL stores relational metadata. Chroma indexes vectors. Test {uuid4()}."
        doc = client.post(
            "/api/documents", files={"file": ("services.txt", content.encode())}
        ).json()
        try:
            client.app.state.worker.process(doc["id"])
            assert client.get(f"/api/documents/{doc['id']}").json()["status"] == "ready"
            result = client.post(
                "/api/chat",
                json={"query": "What stores relational metadata?", "document_ids": [doc["id"]]},
            ).json()
            assert result["grounded"] is True
            assert result["citations"][0]["document_id"] == doc["id"]
            assert client.get(f"/api/conversations/{result['conversation_id']}").status_code == 200
            assert (
                client.delete(f"/api/conversations/{result['conversation_id']}").status_code == 204
            )
        finally:
            assert client.delete(f"/api/documents/{doc['id']}").status_code == 204
