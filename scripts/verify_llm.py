"""Run synthetic, citation-aware end-to-end LLM probes against a running Atlas API.

Uploads and removes only uniquely named probe records. OpenAI runs require --allow-paid.
This is a smoke test with expected fact anchors, not an entailment or accuracy benchmark.
"""

import argparse
import json
import os
import re
import time
from datetime import UTC, datetime
from pathlib import Path
from uuid import uuid4

import httpx

from app.evaluation import require
from scripts.verify_deployment import wait_ready


def stream_answer(client, body, conversations):
    started = time.perf_counter()
    first_token = None
    result = None
    deltas = []
    heartbeats = 0
    source_count = 0
    lines = []
    with client.stream("POST", "/api/chat/stream", json=body) as response:
        response.raise_for_status()
        for line in response.iter_lines():
            if line.startswith(":"):
                heartbeats += 1
                continue
            if line:
                lines.append(line)
                continue
            data = "\n".join(
                item.partition(":")[2].lstrip() for item in lines if item.startswith("data:")
            )
            kind = next(
                (item.partition(":")[2].strip() for item in lines if item.startswith("event:")),
                None,
            )
            lines.clear()
            if not data:
                continue
            event = json.loads(data)
            if kind == "sources":
                conversations.add(event["conversation_id"])
                source_count = len(event["citations"])
            elif kind == "delta":
                if first_token is None and event["text"].strip():
                    first_token = (time.perf_counter() - started) * 1000
                deltas.append(event["text"])
            elif kind == "error":
                raise RuntimeError(event["message"])
            elif kind == "done":
                result = event
    if result is None or "".join(deltas) != result["answer"]:
        raise RuntimeError("Stream did not complete consistently")
    return result, {
        "time_to_first_token_ms": round(first_token, 2) if first_token is not None else None,
        "elapsed_ms": round((time.perf_counter() - started) * 1000, 2),
        "text_deltas": len(deltas),
        "keepalive_comments": heartbeats,
        "retrieved_passages": source_count,
    }


def verify(client, report):
    run_id = report["run_id"]
    documents = []
    conversations = set()
    content = [
        (
            "meridian",
            f"Meridian retains audit logs for 73 days. Meridian backups use AES-256 encryption and run daily at 02:00 UTC. Probe {run_id}.",
        ),
        (
            "borealis",
            f"Borealis retries failed ingestion jobs after 11 seconds. Borealis retains audit logs for 9 days. Probe {run_id}.",
        ),
    ]
    try:
        for name, text in content:
            filename = f"atlas-llm-{run_id}-{name}.txt"
            response = client.post("/api/documents", files={"file": (filename, text.encode())})
            response.raise_for_status()
            document = response.json()
            if document["filename"] != filename:
                raise RuntimeError("Probe upload unexpectedly matched an existing document")
            documents.append(document)
            wait_ready(client, document["id"])
        cases = [
            (
                "retention_paraphrase",
                "After how many days does Meridian delete audit history?",
                0,
                r"\b73[\s-]+days?\b",
                None,
            ),
            (
                "encryption",
                "Which encryption algorithm protects Meridian backups?",
                0,
                r"AES[\s-]?256",
                None,
            ),
            (
                "conversation_followup",
                "And when are its backups scheduled?",
                0,
                r"(?:0?2:00|2\s*a\.?m\.?).*UTC",
                "encryption",
            ),
            (
                "second_document_filter",
                "How long before Borealis retries a failed ingestion job?",
                1,
                r"\b11[\s-]+seconds?\b",
                None,
            ),
            (
                "unanswerable_with_context",
                "Based on Meridian's audit retention and backup policy, who is Meridian's CEO?",
                0,
                r"not |no |cannot|can't|couldn't|unknown|unspecified|unavailable|don't know|do not",
                None,
            ),
        ]
        completed = {}
        for name, question, index, expected, parent in cases:
            row = {"name": name, "question": question, "expected_pattern": expected}
            try:
                body = {"query": question, "document_ids": [documents[index]["id"]], "top_k": 3}
                if parent:
                    body["conversation_id"] = completed[parent]["conversation_id"]
                result, timing = stream_answer(client, body, conversations)
                completed[name] = result
                checks = {
                    "expected_fact_or_abstention": bool(
                        re.search(expected, result["answer"], re.I | re.S)
                    ),
                    "document_filter_respected": all(
                        source["document_id"] == documents[index]["id"]
                        for source in result["citations"]
                    ),
                    "real_context_retrieved": timing["retrieved_passages"] > 0,
                    "configured_provider_used": result["provider"]
                    == report["configuration"]["llm_provider"],
                }
                if name != "unanswerable_with_context":
                    checks["valid_citation_labels"] = result["citation_status"] == "valid" and bool(
                        result["citations"]
                    )
                if parent:
                    checks["memory_query_expanded"] = (
                        completed[parent]["retrieval_query"] in result["retrieval_query"]
                    )
                row.update(
                    {
                        "answer": result["answer"],
                        "citation_status": result["citation_status"],
                        "citations": [
                            {
                                "filename": source["filename"],
                                "page": source["page"],
                                "label": source["label"],
                            }
                            for source in result["citations"]
                        ],
                        "checks": checks,
                        "timing": timing,
                        "passed": all(checks.values()),
                    }
                )
            except Exception as exc:
                row.update({"passed": False, "error": str(exc)})
            report["cases"].append(row)
            print(
                {
                    "case": name,
                    "passed": row["passed"],
                    "elapsed_ms": row.get("timing", {}).get("elapsed_ms"),
                },
                flush=True,
            )
    finally:
        for conversation_id in conversations:
            response = client.delete(f"/api/conversations/{conversation_id}")
            response.raise_for_status()
        for document in documents:
            response = client.delete(f"/api/documents/{document['id']}")
            response.raise_for_status()
        report["probe_records_removed"] = True


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--base-url", default="http://127.0.0.1:8000")
    parser.add_argument("--output", type=Path, default=Path("evaluation/llm-smoke.json"))
    parser.add_argument("--allow-paid", action="store_true")
    args = parser.parse_args()
    headers = {"X-API-Key": os.environ["ATLAS_API_KEY"]} if os.environ.get("ATLAS_API_KEY") else {}
    report = {
        "run_id": str(uuid4()),
        "verified_at": datetime.now(UTC).isoformat(),
        "base_url": args.base_url,
        "warning": "Synthetic expected-fact smoke checks only. Not an entailment, safety, general accuracy, or production latency benchmark.",
        "cases": [],
    }
    args.output.parent.mkdir(parents=True, exist_ok=True)
    with httpx.Client(base_url=args.base_url, headers=headers, timeout=240) as client:
        report["configuration"] = require(client.get("/api/config"))
        provider = report["configuration"]["llm_provider"]
        if provider == "extractive":
            parser.error("Configure a real generation provider first")
        if not args.allow_paid and (
            provider == "openai" or report["configuration"]["embedding_provider"] == "openai"
        ):
            parser.error("Paid providers require an explicit --allow-paid run")
        try:
            verify(client, report)
            report["status"] = (
                "passed"
                if len(report["cases"]) == 5 and all(case["passed"] for case in report["cases"])
                else "failed"
            )
        finally:
            args.output.write_text(json.dumps(report, indent=2) + "\n", encoding="utf-8")
    print(f"Report saved to {args.output}")
    if report["status"] != "passed":
        raise SystemExit(1)


if __name__ == "__main__":
    main()
