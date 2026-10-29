"""Exercise a running deployment, optionally retaining probes to verify a restart.

Run once with --keep, restart the containers, then use --resume on the same report.
Only this script's uniquely named probe documents and conversations are deleted.
Generation must be extractive so this check never makes paid LLM calls.
"""

import argparse
import io
import json
import os
import time
from datetime import UTC, datetime
from pathlib import Path
from uuid import uuid4

import httpx
from pypdf import PdfWriter
from pypdf.generic import DecodedStreamObject, DictionaryObject, NameObject


def timestamp():
    return datetime.now(UTC).isoformat()


def require(response, status=200):
    if response.status_code != status:
        raise AssertionError(
            f"{response.request.method} {response.request.url.path}: {response.status_code}: {response.text[:500]}"
        )
    return response.json() if status != 204 else None


def pdf_probe(run_id):
    writer = PdfWriter()
    font = DictionaryObject(
        {
            NameObject("/Type"): NameObject("/Font"),
            NameObject("/Subtype"): NameObject("/Type1"),
            NameObject("/BaseFont"): NameObject("/Helvetica"),
        }
    )
    for sentence in (
        f"Aurora is a document storage engine. Architecture overview. Probe {run_id}.",
        "The Aurora storage engine keeps audit records for 73 days.",
    ):
        page = writer.add_blank_page(width=612, height=792)
        page[NameObject("/Resources")] = DictionaryObject(
            {NameObject("/Font"): DictionaryObject({NameObject("/F1"): writer._add_object(font)})}
        )
        stream = DecodedStreamObject()
        stream.set_data(f"BT /F1 12 Tf 72 720 Td ({sentence}) Tj ET".encode())
        page[NameObject("/Contents")] = writer._add_object(stream)
    target = io.BytesIO()
    writer.write(target)
    return target.getvalue()


def wait_ready(client, document_id, timeout=180):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        document = require(client.get(f"/api/documents/{document_id}"))
        if document["status"] == "ready":
            return document
        if document["status"] == "failed":
            raise AssertionError(f"Probe ingestion failed: {document['error']}")
        time.sleep(0.25)
    raise TimeoutError(f"Document {document_id} did not become ready")


def validate_search(client, document_id):
    hits = require(
        client.post(
            "/api/search",
            json={
                "query": "What is the audit retention period for Aurora?",
                "document_ids": [document_id],
                "top_k": 3,
            },
        )
    )
    assert hits and all(hit["document_id"] == document_id for hit in hits)
    assert any(hit["page"] == 2 and "73 days" in hit["text"] for hit in hits)


def seed_and_check(client, state):
    prefix = f"atlas-smoke-{state['run_id']}"
    probes = [
        (f"{prefix}.pdf", pdf_probe(state["run_id"])),
        (
            f"{prefix}.md",
            f"Borealis queue retries failed ingestion jobs after 11 seconds. Probe {state['run_id']}.".encode(),
        ),
    ]
    for filename, content in probes:
        document = require(client.post("/api/documents", files={"file": (filename, content)}), 202)
        state["documents"].append({"id": document["id"], "filename": filename})
    documents = [wait_ready(client, item["id"]) for item in state["documents"]]
    assert documents[0]["page_count"] == 2
    duplicate = require(client.post("/api/documents", files={"file": probes[0]}), 202)
    assert duplicate["id"] == documents[0]["id"]
    state["checks"].extend(
        ["pdf_and_markdown_ingestion", "content_deduplication", "pdf_page_provenance"]
    )
    validate_search(client, documents[0]["id"])
    state["checks"].append("document_filtered_retrieval")
    answer = require(
        client.post(
            "/api/chat",
            json={
                "query": "How long does Aurora keep audit records?",
                "document_ids": [documents[0]["id"]],
            },
        )
    )
    state["conversations"].append(answer["conversation_id"])
    assert "73 days" in answer["answer"] and answer["citation_status"] == "valid"
    assert any(
        c["page"] == 2 and c["document_id"] == documents[0]["id"] for c in answer["citations"]
    )
    followup = require(
        client.post(
            "/api/chat",
            json={
                "query": "What about its audit records?",
                "conversation_id": answer["conversation_id"],
                "document_ids": [documents[0]["id"]],
            },
        )
    )
    assert "How long does Aurora" in followup["retrieval_query"]
    history = require(client.get(f"/api/conversations/{answer['conversation_id']}"))
    assert [message["role"] for message in history["messages"]] == [
        "user",
        "assistant",
        "user",
        "assistant",
    ]
    state["checks"].extend(["page_cited_answer", "followup_memory", "persisted_conversation"])
    response = client.post(
        "/api/chat/stream",
        json={
            "query": "How long before Borealis retries failed jobs?",
            "document_ids": [documents[1]["id"]],
        },
    )
    response.raise_for_status()
    events = []
    for block in response.text.replace("\r\n", "\n").strip().split("\n\n"):
        lines = block.splitlines()
        name = next(line[7:] for line in lines if line.startswith("event: "))
        data = json.loads("\n".join(line[6:] for line in lines if line.startswith("data: ")))
        events.append((name, data))
        if name == "sources":
            state["conversations"].append(data["conversation_id"])
    assert events[0][0] == "sources" and events[-1][0] == "done"
    assert (
        "".join(data["text"] for name, data in events if name == "delta") == events[-1][1]["answer"]
    )
    assert "11 seconds" in events[-1][1]["answer"]
    state["checks"].append("streaming_event_contract")
    state["seed_verified_at"] = timestamp()


def verify_restart(client, state):
    for item in state["documents"]:
        document = wait_ready(client, item["id"])
        assert document["filename"] == item["filename"]
    validate_search(client, state["documents"][0]["id"])
    history = require(client.get(f"/api/conversations/{state['conversations'][0]}"))
    assert len(history["messages"]) == 4
    assert any("73 days" in message["content"] for message in history["messages"])
    # Reindex proves source files survived, in addition to SQL and vector persistence.
    require(client.post(f"/api/documents/{state['documents'][0]['id']}/reindex"), 202)
    wait_ready(client, state["documents"][0]["id"])
    validate_search(client, state["documents"][0]["id"])
    state["checks"].extend(
        [
            "sql_and_vector_persistence_after_restart",
            "conversation_persistence_after_restart",
            "source_file_persistence_and_reindex",
        ]
    )
    state["persistence_verified_at"] = timestamp()


def cleanup(client, state):
    prefix = f"atlas-smoke-{state['run_id']}"
    for item in state["documents"]:
        document = require(client.get(f"/api/documents/{item['id']}"))
        if document["filename"] != item["filename"] or not document["filename"].startswith(prefix):
            raise ValueError("Refusing to clean up a document outside this probe run")
    for conversation_id in dict.fromkeys(state["conversations"]):
        require(client.delete(f"/api/conversations/{conversation_id}"), 204)
    for item in state["documents"]:
        require(client.delete(f"/api/documents/{item['id']}"), 204)
        assert client.get(f"/api/documents/{item['id']}").status_code == 404
        hits = require(
            client.post(
                "/api/search",
                json={"query": "Aurora Borealis audit retention", "document_ids": [item["id"]]},
            )
        )
        assert hits == []
    state["cleaned_up"] = True
    state["checks"].append("probe_deletion_and_retrieval_exclusion")


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--base-url", default="http://127.0.0.1:8000")
    parser.add_argument("--output", type=Path)
    parser.add_argument(
        "--keep", action="store_true", help="Keep probes to test container restart persistence"
    )
    parser.add_argument(
        "--resume", type=Path, help="Verify retained probes after a restart, then clean up"
    )
    args = parser.parse_args()
    output = args.output or args.resume or Path("evaluation/deployment-smoke.json")
    state = (
        json.loads(args.resume.read_text(encoding="utf-8"))
        if args.resume
        else {
            "run_id": str(uuid4()),
            "base_url": args.base_url,
            "documents": [],
            "conversations": [],
            "checks": [],
            "cleaned_up": False,
        }
    )
    if args.resume and (state["base_url"] != args.base_url or state["cleaned_up"]):
        parser.error("Resume requires the original base URL and an uncleaned probe run")
    headers = {"X-API-Key": os.environ["ATLAS_API_KEY"]} if os.environ.get("ATLAS_API_KEY") else {}
    output.parent.mkdir(parents=True, exist_ok=True)
    with httpx.Client(base_url=args.base_url, headers=headers, timeout=180) as client:
        require(client.get("/api/health"))
        state["configuration"] = require(client.get("/api/config"))
        if state["configuration"]["llm_provider"] != "extractive":
            parser.error("Use extractive generation for this repeatable, no-paid-LLM smoke test")
        try:
            if args.resume:
                verify_restart(client, state)
            else:
                seed_and_check(client, state)
            if not args.keep:
                cleanup(client, state)
            state["status"] = "passed"
        except Exception:
            state["status"] = "failed"
            raise
        finally:
            output.write_text(json.dumps(state, indent=2) + "\n", encoding="utf-8")
    print(
        json.dumps(
            {
                "status": state["status"],
                "checks": state["checks"],
                "cleaned_up": state["cleaned_up"],
            },
            indent=2,
        )
    )
    print(f"Report saved to {output}")


if __name__ == "__main__":
    main()
