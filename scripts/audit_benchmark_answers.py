"""Capture a fixed small local-LLM answer sample for manual claim/evidence review.

No automatic answer or entailment score is assigned. Run only after retrieval finishes.
"""

import argparse
import hashlib
import json
import os
from pathlib import Path

import httpx

from app.benchmark import canonical_hits, require, save, timestamp
from scripts.verify_llm import stream_answer


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--base-url", default="http://127.0.0.1:8002")
    parser.add_argument("--directory", type=Path, default=Path("evaluation/nist-v1"))
    parser.add_argument("--state", type=Path, default=Path(".cache/nist-v1-run.json"))
    args = parser.parse_args()
    output = args.directory / "answer-audit.json"
    if output.exists():
        raise FileExistsError("Answer audit already exists; preserve the original observation")
    retrieval = json.loads(
        (args.directory / "retrieval-fastembed.json").read_text(encoding="utf-8")
    )
    protocol = json.loads(
        (args.directory / "answer-audit-protocol.json").read_text(encoding="utf-8")
    )
    question_path = args.directory / "questions.json"
    fingerprint = hashlib.sha256(question_path.read_bytes()).hexdigest()
    if (
        retrieval["status"] != "complete"
        or fingerprint != retrieval["fingerprints"]["questions_sha256"]
    ):
        raise ValueError("A completed retrieval run with these exact questions is required")
    if fingerprint != protocol["questions_sha256"]:
        raise ValueError("Audit protocol and questions differ")
    questions = {
        q["id"]: q for q in json.loads(question_path.read_text(encoding="utf-8"))["questions"]
    }
    state = json.loads(args.state.read_text(encoding="utf-8"))
    ids = state["document_ids"]
    audit_state_path = args.state.with_name("nist-answer-audit-state.json")
    audit_state = {"conversations": []}
    headers = {"X-API-Key": os.environ["ATLAS_API_KEY"]} if os.environ.get("ATLAS_API_KEY") else {}
    report = {
        "started_at": timestamp(),
        "questions_sha256": fingerprint,
        "retrieval_report_sha256": hashlib.sha256(
            (args.directory / "retrieval-fastembed.json").read_bytes()
        ).hexdigest(),
        "selected_mode": retrieval["selected_mode"],
        "review_status": "pending manual review",
        "scoring": "No automatic factual-accuracy or entailment score. Citation status is structural only.",
        "questions": [],
    }
    with httpx.Client(base_url=args.base_url.rstrip("/"), headers=headers, timeout=240) as client:
        config = require(client.get("/api/config"))
        if config["llm_provider"] != "ollama" or config["embedding_provider"] != "fastembed":
            raise ValueError(
                "This audit is restricted to local Ollama generation and FastEmbed embeddings"
            )
        if config != retrieval["configuration"]:
            raise ValueError("Configuration differs from the frozen retrieval run")
        report["configuration"] = config
        for document_id in ids.values():
            item = require(client.get(f"/api/documents/{document_id}"))
            if item["status"] != "ready" or item["index_signature"] != config["index_signature"]:
                raise ValueError("Audit corpus is not ready in the frozen index")
        save(output, report)
        for question_id in protocol["question_ids"]:
            q = questions[question_id]
            row = {
                "id": q["id"],
                "query": q["query"],
                "answerable": q["answerable"],
                "expected_answer": q["expected_answer"],
                "labeled_evidence": q["evidence"],
            }
            conversations = set()
            try:
                answer, stream_metrics = stream_answer(
                    client,
                    {
                        "query": q["query"],
                        "mode": retrieval["selected_mode"],
                        "top_k": 5,
                        "document_ids": list(ids.values()),
                    },
                    conversations,
                )
                row["stream_metrics"] = stream_metrics
                # Save actual bounded citation snapshots, preserving labels for a human reviewer.
                row.update(
                    {
                        key: answer[key]
                        for key in (
                            "answer",
                            "citation_status",
                            "grounded",
                            "latency_ms",
                            "provider",
                        )
                    }
                )
                row["citations"] = [
                    dict(hit, label=original["label"])
                    for hit, original in zip(
                        canonical_hits(answer["citations"], ids), answer["citations"], strict=True
                    )
                ]
            except (httpx.HTTPError, ValueError, RuntimeError) as exc:
                row["error"] = f"{type(exc).__name__}: {exc}"
            finally:
                # The first SSE event supplies IDs even if generation subsequently fails.
                # Keep them in ignored local state so an interrupted cleanup is recoverable.
                row["conversation_cleanup_statuses"] = []
                for conversation_id in conversations:
                    record = {"question_id": q["id"], "id": conversation_id, "deleted": False}
                    audit_state["conversations"].append(record)
                    save(audit_state_path, audit_state)
                    try:
                        status = client.delete(f"/api/conversations/{conversation_id}").status_code
                        record["deleted"] = status in (204, 404)
                        row["conversation_cleanup_statuses"].append(status)
                    except httpx.HTTPError:
                        row["conversation_cleanup_statuses"].append("connection_error")
                    save(audit_state_path, audit_state)
            report["questions"].append(row)
            save(output, report)
            print(f"Captured {q['id']}: {row.get('citation_status', row.get('error'))}", flush=True)
    report["finished_at"] = timestamp()
    save(output, report)


if __name__ == "__main__":
    main()
