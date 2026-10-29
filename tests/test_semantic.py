import os
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from app.main import create_app


@pytest.mark.skipif(
    os.environ.get("ATLAS_TEST_SEMANTIC") != "1", reason="Optional local model test"
)
def test_semantic_search_matches_meaning_without_exact_words(settings):
    settings.embedding_provider = "fastembed"
    settings.embedding_model = "BAAI/bge-small-en-v1.5"
    settings.chunk_strategy = "semantic"
    settings.model_cache_dir = Path("data/models").resolve()
    with TestClient(create_app(settings)) as client:
        documents = []
        for filename, content in [
            ("medical.txt", "A physician diagnoses illnesses and treats patients in a hospital."),
            (
                "database.txt",
                "An SQL database stores customer records with indexes and relational tables.",
            ),
        ]:
            doc = client.post("/api/documents", files={"file": (filename, content.encode())}).json()
            client.app.state.worker.process(doc["id"])
            documents.append(doc)
        body = {"query": "A doctor provides medical care", "mode": "vector", "top_k": 1}
        hits = client.post("/api/search", json=body).json()
        assert hits and hits[0]["filename"] == "medical.txt"
        assert hits[0]["vector_score"] > 0.6
        # This question has no content words in common with the physician passage.
        keyword = client.post(
            "/api/search", json={**body, "mode": "keyword", "document_ids": [documents[0]["id"]]}
        ).json()
        assert keyword == []
