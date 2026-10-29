"""Reproducible passage-level retrieval metrics, without LLM judge calls."""

import argparse
import hashlib
import json
import math
import os
import statistics
import tempfile
import time
from datetime import UTC, datetime
from pathlib import Path

import httpx


def measure(hits: list[dict], labels: list[dict], k: int) -> dict:
    if not labels:
        raise ValueError("Each evaluation question needs at least one relevant label")
    found = set()
    gains = []
    first = None
    for rank, hit in enumerate(hits[:k], 1):
        matches = {
            i
            for i, label in enumerate(labels)
            if hit["filename"] == label["filename"]
            and ("page" not in label or hit["page"] == label["page"])
            and label.get("contains", "").casefold() in hit["text"].casefold()
        }
        fresh = matches - found
        if matches and first is None:
            first = rank
        gains.append(1 if fresh else 0)
        found.update(matches)
    dcg = sum(gain / math.log2(rank + 1) for rank, gain in enumerate(gains, 1))
    ideal = sum(1 / math.log2(rank + 1) for rank in range(1, min(len(labels), k) + 1))
    return {
        "recall_at_k": len(found) / len(labels),
        "precision_at_k": sum(gains) / k,
        "reciprocal_rank": 1 / first if first else 0,
        "ndcg_at_k": dcg / ideal if ideal else 0,
        "hit_at_k": int(bool(found)),
    }


def require(response):
    response.raise_for_status()
    return response.json()


def seed(client, directory: Path, timeout: int = 180):
    ids = []
    for path in sorted(directory.iterdir()):
        if path.suffix.lower() not in (".pdf", ".md", ".txt"):
            continue
        with path.open("rb") as source:
            item = require(client.post("/api/documents", files={"file": (path.name, source)}))
        ids.append(item["id"])
        if hasattr(client, "app") and not client.app.state.settings.worker_enabled:
            client.app.state.worker.process(item["id"])
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        documents = [require(client.get(f"/api/documents/{value}")) for value in ids]
        failed = [d for d in documents if d["status"] == "failed"]
        if failed:
            raise RuntimeError(f"Sample ingestion failed: {failed}")
        if all(d["status"] == "ready" for d in documents):
            return ids
        time.sleep(0.2)
    raise TimeoutError("Sample ingestion did not finish before the timeout")


def evaluate(client, questions: list[dict], k=5, modes=("hybrid",), document_ids=None):
    config = require(client.get("/api/config"))
    stats = require(client.get("/api/stats"))
    report = {
        "created_at": datetime.now(UTC).isoformat(),
        "top_k": k,
        "question_count": len(questions),
        "configuration": config,
        "corpus": stats,
        "warning": "Small synthetic smoke benchmark; not evidence of general retrieval accuracy or production latency.",
        "modes": {},
    }
    for mode in modes:
        measurements = []
        for question in questions:
            start = time.perf_counter()
            hits = require(
                client.post(
                    "/api/search",
                    json={
                        "query": question["query"],
                        "top_k": k,
                        "mode": mode,
                        "document_ids": document_ids or [],
                    },
                )
            )
            elapsed = (time.perf_counter() - start) * 1000
            measurements.append(
                {
                    "query": question["query"],
                    **measure(hits, question["relevant"], k),
                    "latency_ms": round(elapsed, 2),
                    "retrieved": [
                        {"filename": h["filename"], "page": h["page"], "chunk_id": h["chunk_id"]}
                        for h in hits
                    ],
                }
            )
        keys = ("recall_at_k", "precision_at_k", "reciprocal_rank", "ndcg_at_k", "hit_at_k")
        summary = {key: round(statistics.mean(row[key] for row in measurements), 4) for key in keys}
        latencies = sorted(row["latency_ms"] for row in measurements)
        summary["median_latency_ms"] = statistics.median(latencies)
        summary["p95_latency_ms"] = latencies[max(0, math.ceil(len(latencies) * 0.95) - 1)]
        report["modes"][mode] = {"summary": summary, "questions": measurements}
    return report


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--base-url", default="http://127.0.0.1:8000")
    parser.add_argument("--dataset", type=Path, default=Path("evaluation/questions.json"))
    parser.add_argument("--samples", type=Path, default=Path("samples"))
    parser.add_argument(
        "--seed", action="store_true", help="Upload sample files to a running workspace"
    )
    parser.add_argument(
        "--local", action="store_true", help="Use a disposable local workspace and seed samples"
    )
    parser.add_argument("--top-k", type=int, choices=range(1, 13), default=5)
    parser.add_argument("--mode", choices=["hybrid", "vector", "keyword", "all"], default="all")
    parser.add_argument("--output", type=Path, default=Path("evaluation/latest.json"))
    args = parser.parse_args()
    questions = json.loads(args.dataset.read_text(encoding="utf-8"))
    if not questions:
        parser.error("Dataset is empty")
    modes = ("keyword", "vector", "hybrid") if args.mode == "all" else (args.mode,)
    if args.local:
        from fastapi.testclient import TestClient

        from app.config import Settings
        from app.main import create_app

        with tempfile.TemporaryDirectory(prefix="atlas-eval-") as directory:
            settings = Settings(
                data_dir=Path(directory),
                database_url=f"sqlite:///{Path(directory) / 'eval.db'}",
                worker_enabled=False,
                api_key=None,
                model_cache_dir=Path("data/models").resolve(),
            )
            with TestClient(create_app(settings)) as client:
                document_ids = seed(client, args.samples)
                report = evaluate(client, questions, args.top_k, modes, document_ids)
    else:
        headers = (
            {"X-API-Key": os.environ["ATLAS_API_KEY"]} if os.environ.get("ATLAS_API_KEY") else {}
        )
        with httpx.Client(
            base_url=args.base_url.rstrip("/"), headers=headers, timeout=180
        ) as client:
            document_ids = seed(client, args.samples) if args.seed else None
            report = evaluate(client, questions, args.top_k, modes, document_ids)
    report["dataset_sha256"] = hashlib.sha256(args.dataset.read_bytes()).hexdigest()
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(report, indent=2) + "\n", encoding="utf-8")
    print(json.dumps({mode: row["summary"] for mode, row in report["modes"].items()}, indent=2))
    print(f"Report saved to {args.output}")


if __name__ == "__main__":
    main()
