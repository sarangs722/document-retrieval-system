from uuid import uuid4

from fastapi.testclient import TestClient

from app.main import create_app
from scripts.verify_deployment import cleanup, seed_and_check, verify_restart


def test_deployment_probe_and_restart(settings):
    settings.worker_enabled = True
    state = {"run_id": str(uuid4()), "documents": [], "conversations": [], "checks": []}
    with TestClient(create_app(settings)) as client:
        seed_and_check(client, state)
    with TestClient(create_app(settings)) as client:
        verify_restart(client, state)
        cleanup(client, state)
        assert state["cleaned_up"] is True
        assert client.get("/api/stats").json()["documents"] == 0
        assert client.get("/api/stats").json()["conversations"] == 0
        assert client.app.state.vectors.collection.count() == 0
