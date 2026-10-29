import pytest
from fastapi.testclient import TestClient

from app.config import Settings
from app.main import create_app


@pytest.fixture
def settings(tmp_path):
    return Settings(
        _env_file=None,
        data_dir=tmp_path,
        database_url=f"sqlite:///{tmp_path / 'test.db'}",
        embedding_provider="hash",
        llm_provider="extractive",
        chunk_strategy="sentence",
        worker_enabled=False,
    )


@pytest.fixture
def client(settings):
    with TestClient(create_app(settings)) as client:
        yield client


@pytest.fixture
def ingest(client):
    def add(
        filename="manual.txt",
        text="Document ingestion uses PDF parsing and semantic chunking. Citations reference source pages.",
    ):
        response = client.post(
            "/api/documents", files={"file": (filename, text.encode(), "text/plain")}
        )
        assert response.status_code == 202, response.text
        item = response.json()
        client.app.state.worker.process(item["id"])
        result = client.get(f"/api/documents/{item['id']}").json()
        assert result["status"] == "ready", result
        return result

    return add
