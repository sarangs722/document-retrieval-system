"""Pinned-PDF benchmark: validate labels, freeze dev selection, then evaluate test queries.

Run from the repository root with python -m app.benchmark --help.
No generation calls are made. Source PDFs and local run IDs stay under ignored directories.
"""

import argparse
import hashlib
import json
import math
import os
import statistics
import time
import unicodedata
from datetime import UTC, datetime
from pathlib import Path
from typing import Literal
from urllib.parse import urlsplit

import httpx
from pydantic import BaseModel, Field, StrictBool, model_validator

from app.config import Settings
from app.parsing import parse

MODES = ("keyword", "vector", "hybrid")
TOP_K = 5


def timestamp():
    return datetime.now(UTC).isoformat()


def digest(path: Path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def canonical(text: str):
    return " ".join(unicodedata.normalize("NFKC", text).replace("\u00ad", "").casefold().split())


def save(path: Path, value):
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".part")
    temporary.write_text(json.dumps(value, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    temporary.replace(path)


class Source(BaseModel):
    id: str = Field(pattern=r"^[a-z0-9-]+$")
    split: Literal["dev", "test"]
    filename: str = Field(pattern=r"^[a-z0-9.-]+\.pdf$")
    title: str
    url: str
    sha256: str = Field(pattern=r"^[a-f0-9]{64}$")
    page_count: int = Field(gt=0, le=200)
    size_bytes: int = Field(gt=0, le=20 * 1024 * 1024)

    @model_validator(mode="after")
    def official_source(self):
        url = urlsplit(self.url)
        if (
            url.scheme != "https"
            or url.netloc != "nvlpubs.nist.gov"
            or not url.path.lower().endswith(".pdf")
            or url.query
            or url.fragment
            or self.filename.startswith(".")
        ):
            raise ValueError("Sources must be official NIST HTTPS PDFs with safe local filenames")
        return self


class Evidence(BaseModel):
    document_id: str
    page: int = Field(gt=0)
    quote: str = Field(min_length=8)


class Question(BaseModel):
    id: str
    split: Literal["dev", "test"]
    category: Literal["fact", "paraphrase", "unanswerable"]
    topic_document_id: str
    query: str = Field(min_length=8, max_length=4000)
    answerable: StrictBool
    expected_answer: str = Field(min_length=1)
    evidence: list[Evidence]
    reason: str | None = None
    absent_term: str | None = None

    @model_validator(mode="after")
    def judgments(self):
        if self.answerable != bool(self.evidence):
            raise ValueError("Answerable questions require evidence; negatives must have none")
        if (self.category == "unanswerable") == self.answerable:
            raise ValueError("Category and answerability disagree")
        if not self.answerable and (not self.reason or not self.absent_term):
            raise ValueError("Negatives require an explicit reason and a corpus-absent identifier")
        return self


def load_dataset(manifest_path: Path, questions_path: Path):
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    dataset = json.loads(questions_path.read_text(encoding="utf-8"))
    if manifest["name"] != dataset["name"] or manifest["version"] != dataset["version"]:
        raise ValueError("Manifest and question versions differ")
    sources = [Source.model_validate(row) for row in manifest["documents"]]
    questions = [Question.model_validate(row) for row in dataset["questions"]]
    if not sources or not questions:
        raise ValueError("Empty corpus or questions")
    if len({s.id for s in sources}) != len(sources) or len({s.filename for s in sources}) != len(
        sources
    ):
        raise ValueError("Duplicate source IDs or filenames")
    if len({q.id for q in questions}) != len(questions):
        raise ValueError("Duplicate question IDs")
    if len({canonical(q.query) for q in questions}) != len(questions):
        raise ValueError("Duplicate queries")
    by_id = {source.id: source for source in sources}
    for q in questions:
        if q.topic_document_id not in by_id or by_id[q.topic_document_id].split != q.split:
            raise ValueError(f"Question {q.id} violates the document-disjoint split")
        for evidence in q.evidence:
            source = by_id.get(evidence.document_id)
            if not source or source.split != q.split or evidence.page > source.page_count:
                raise ValueError(f"Invalid evidence or cross-split label for {q.id}")
    for split in ("dev", "test"):
        subset = [q for q in questions if q.split == split]
        if not any(q.answerable for q in subset) or not any(not q.answerable for q in subset):
            raise ValueError("Each split needs positives and negatives")
    return sources, questions


def verify_file(path: Path, source: Source):
    if (
        not path.is_file()
        or path.stat().st_size != source.size_bytes
        or digest(path) != source.sha256
    ):
        raise ValueError(f"Missing or checksum-mismatched source: {source.filename}")
    with path.open("rb") as stream:
        if not stream.read(1024).startswith(b"%PDF-"):
            raise ValueError(f"Not a PDF: {source.filename}")


def download_sources(sources: list[Source], directory: Path):
    directory.mkdir(parents=True, exist_ok=True)
    # No redirects: a manifest URL may not redirect to an unreviewed host.
    with httpx.Client(timeout=120, follow_redirects=False) as client:
        for source in sources:
            target = directory / source.filename
            if target.exists():
                verify_file(target, source)
                continue
            temporary = target.with_suffix(".pdf.part")
            try:
                size = 0
                with client.stream("GET", source.url) as response, temporary.open("wb") as out:
                    response.raise_for_status()
                    for block in response.iter_bytes():
                        size += len(block)
                        if size > source.size_bytes:
                            raise ValueError(f"Download exceeds pinned size: {source.id}")
                        out.write(block)
                verify_file(temporary, source)
                temporary.replace(target)
            finally:
                temporary.unlink(missing_ok=True)
            print(f"Downloaded and verified {source.id}", flush=True)


def validate_evidence(sources, questions, directory):
    settings = Settings(_env_file=None)
    pages = {}
    for source in sources:
        path = directory / source.filename
        verify_file(path, source)
        parsed = parse(path, ".pdf", settings)
        if len(parsed) != source.page_count:
            raise ValueError(f"Page count changed: {source.id}")
        pages[source.id] = [canonical(page.text) for page in parsed]
    for q in questions:
        for label in q.evidence:
            if canonical(label.quote) not in pages[label.document_id][label.page - 1]:
                raise ValueError(f"Evidence quote not found on labeled PDF page: {q.id}")
        if not q.answerable and any(
            canonical(q.absent_term) in page for document in pages.values() for page in document
        ):
            raise ValueError(f"Negative identifier unexpectedly present in corpus: {q.id}")
    return {
        "documents": len(pages),
        "physical_pages": sum(map(len, pages.values())),
        "questions": len(questions),
        "evidence_quotes": sum(len(q.evidence) for q in questions),
    }


def matching_rank(hits: list[dict], question: Question):
    """One required fact; evidence entries are alternative anchors, never separate recall targets."""
    if not question.answerable:
        return None
    for rank, hit in enumerate(hits[:TOP_K], 1):
        if any(
            hit["document_id"] == label.document_id
            and hit["page"] == label.page
            and canonical(label.quote) in canonical(hit["text"])
            for label in question.evidence
        ):
            return rank
    return None


def summarize(rows: list[dict]):
    positives = [r for r in rows if r["answerable"]]
    negatives = [r for r in rows if not r["answerable"]]
    if not positives or not negatives:
        raise ValueError("Metrics need separate nonempty positive and negative groups")
    ranks = [r["first_evidence_rank"] for r in positives]
    latency = sorted(r["latency_ms"] for r in rows)
    return {
        "answerable_questions": len(positives),
        "unanswerable_questions": len(negatives),
        **{
            f"evidence_hit_at_{k}": sum(r is not None and r <= k for r in ranks) / len(ranks)
            for k in (1, 3, 5)
        },
        "mrr_at_5": statistics.mean(1 / r if r else 0 for r in ranks),
        "negative_empty_result_rate": sum(not r["retrieved"] for r in negatives) / len(negatives),
        "median_latency_ms": statistics.median(latency),
        "p95_latency_ms": latency[math.ceil(len(latency) * 0.95) - 1],
        "missed_question_ids": [r["id"] for r in positives if r["first_evidence_rank"] is None],
    }


def require(response):
    response.raise_for_status()
    return response.json()


def existing_ids(client):
    ids = set()
    offset = 0
    while True:
        batch = require(client.get("/api/documents", params={"limit": 200, "offset": offset}))
        ids.update(item["id"] for item in batch)
        if len(batch) < 200:
            return ids
        offset += len(batch)


def ingest_sources(client, sources, directory, state, state_path, timeout):
    before = existing_ids(client)
    state["preexisting_ids"] = sorted(before)
    save(state_path, state)
    for source in sources:
        with (directory / source.filename).open("rb") as stream:
            item = require(client.post("/api/documents", files={"file": (source.filename, stream)}))
        state["document_ids"][source.id] = item["id"]
        if item["id"] not in before:
            state["created_ids"].append(item["id"])
        save(state_path, state)
        deadline = time.monotonic() + timeout
        last_print = 0
        while item["status"] != "ready":
            if item["status"] == "failed":
                raise RuntimeError(f"Ingestion failed for {source.id}: {item['error']}")
            if time.monotonic() > deadline:
                raise TimeoutError(
                    f"Ingestion timeout for {source.id}; run IDs retained in {state_path}"
                )
            if time.monotonic() - last_print > 30:
                print(f"Indexing {source.id}: {item['status']}", flush=True)
                last_print = time.monotonic()
            time.sleep(1)
            item = require(client.get(f"/api/documents/{item['id']}"))
        state["corpus"].append(
            {
                "id": source.id,
                "page_count": item["page_count"],
                "chunk_count": item["chunk_count"],
                "index_signature": item["index_signature"],
            }
        )
        save(state_path, state)
        print(
            f"Ready {source.id}: {item['page_count']} pages, {item['chunk_count']} chunks",
            flush=True,
        )


def canonical_hits(hits, document_ids):
    inverse = {value: key for key, value in document_ids.items()}
    if any(hit["document_id"] not in inverse for hit in hits):
        raise RuntimeError("Search returned a document outside the benchmark corpus")
    return [
        {
            "document_id": inverse[h["document_id"]],
            "page": h["page"],
            "text": h["text"],
            "text_sha256": hashlib.sha256(h["text"].encode()).hexdigest(),
            "score": h["score"],
            "keyword_score": h.get("keyword_score"),
            "vector_score": h.get("vector_score"),
        }
        for h in hits
    ]


def index_coverage(client, questions, document_ids):
    chunks = []
    for document_id in document_ids.values():
        offset = 0
        while True:
            batch = require(
                client.get(
                    f"/api/documents/{document_id}/chunks", params={"limit": 100, "offset": offset}
                )
            )
            # This endpoint's document provenance is the requested resource, not a payload field.
            stable_id = next(key for key, value in document_ids.items() if value == document_id)
            chunks.extend({**chunk, "document_id": stable_id} for chunk in batch)
            if len(batch) < 100:
                break
            offset += len(batch)
    # matching_rank intentionally considers top five only; coverage checks all chunks separately.
    missing = [
        q.id
        for q in questions
        if q.answerable and not any(matching_rank([chunk], q) is not None for chunk in chunks)
    ]
    return {
        "chunks": len(chunks),
        "answerable_questions": sum(q.answerable for q in questions),
        "questions_without_an_intact_evidence_anchor": missing,
    }


def evaluate_split(client, questions, split, document_ids):
    result = {}
    for mode in MODES:
        rows = []
        for q in (q for q in questions if q.split == split):
            start = time.perf_counter()
            hits = canonical_hits(
                require(
                    client.post(
                        "/api/search",
                        json={
                            "query": q.query,
                            "top_k": TOP_K,
                            "mode": mode,
                            "document_ids": list(document_ids.values()),
                        },
                    )
                ),
                document_ids,
            )
            rows.append(
                {
                    "id": q.id,
                    "category": q.category,
                    "answerable": q.answerable,
                    "first_evidence_rank": matching_rank(hits, q),
                    "latency_ms": round((time.perf_counter() - start) * 1000, 2),
                    "retrieved": hits,
                }
            )
        result[mode] = {"summary": summarize(rows), "questions": rows}
        print(f"{split} {mode}: {json.dumps(result[mode]['summary'])}", flush=True)
    return result


def choose_mode(dev):
    # Fixed tie priority uses the predeclared MODES order; test results never enter selection.
    return max(MODES, key=lambda mode: dev[mode]["summary"]["mrr_at_5"])


def run(
    client,
    sources,
    questions,
    directory,
    output,
    state_path,
    fingerprints,
    *,
    allow_paid=False,
    timeout=1800,
    cleanup=False,
):
    selection_path = output.with_suffix(".selection.json")
    if output.exists() or selection_path.exists() or state_path.exists():
        raise FileExistsError(
            "Use new output and state paths; benchmark artifacts are never overwritten"
        )
    config = require(client.get("/api/config"))
    if config["embedding_provider"] == "openai" and not allow_paid:
        raise ValueError("Paid embeddings require --allow-paid; no LLM calls are made")
    local_hashes = {
        name: digest(Path(__file__).parent / name)
        for name in ("parsing.py", "embeddings.py", "retrieval.py")
    }
    if config.get("implementation_sha256") != local_hashes:
        raise ValueError(
            "Deploy this source version before benchmarking; server implementation differs"
        )
    state = {"started_at": timestamp(), "document_ids": {}, "created_ids": [], "corpus": []}
    report = {
        "status": "running",
        "started_at": timestamp(),
        "fingerprints": fingerprints,
        "configuration": config,
        "protocol": {
            "top_k": TOP_K,
            "modes": MODES,
            "selection": "Maximum dev MRR@5; ties: keyword, vector, hybrid",
            "split": "Document-disjoint questions; all source documents are retrieval candidates in both splits",
            "relevance": "Same stable document ID, physical PDF page, and normalized exact quote; alternatives are OR",
            "limitations": "Agent-authored, non-exhaustive labels; not independently human-adjudicated. Negative retrieval rates are not answer/refusal accuracy. No generation or entailment scoring. Latencies are sequential local observations, not a load test.",
        },
    }
    save(output, report)
    try:
        ingest_sources(client, sources, directory, state, state_path, timeout)
        if any(row["index_signature"] != config["index_signature"] for row in state["corpus"]):
            raise RuntimeError("Benchmark documents have a stale index signature")
        report["corpus"] = state["corpus"]
        report["index_coverage"] = index_coverage(client, questions, state["document_ids"])
        report["dev"] = evaluate_split(client, questions, "dev", state["document_ids"])
        selection = {
            "locked_at": timestamp(),
            "fingerprints": fingerprints,
            "configuration": config,
            "selected_mode": choose_mode(report["dev"]),
            "dev_summaries": {mode: value["summary"] for mode, value in report["dev"].items()},
            "rule": report["protocol"]["selection"],
        }
        # Persist selection BEFORE issuing the first held-out query.
        save(selection_path, selection)
        report["selection_sha256"] = digest(selection_path)
        report["selected_mode"] = selection["selected_mode"]
        report["test_started_at"] = timestamp()
        save(output, report)
        report["test"] = evaluate_split(client, questions, "test", state["document_ids"])
        if require(client.get("/api/config")) != config:
            raise RuntimeError("Server configuration changed during the benchmark")
        report["status"] = "complete"
    except BaseException as exc:
        report["status"] = "failed"
        report["error"] = f"{type(exc).__name__}: {exc}"
        raise
    finally:
        failures = []
        if cleanup:
            for document_id in dict.fromkeys(state["created_ids"]):
                try:
                    response = client.delete(f"/api/documents/{document_id}")
                    if response.status_code not in (204, 404):
                        failures.append(document_id)
                except httpx.HTTPError:
                    failures.append(document_id)
        state["cleanup_requested"] = cleanup
        state["cleanup_failed_ids"] = failures
        save(state_path, state)
        report["finished_at"] = timestamp()
        report["documents_retained"] = not cleanup or bool(failures)
        report["cleanup_failed_count"] = len(failures)
        save(output, report)
    return report


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--manifest", type=Path, default=Path("evaluation/nist-v1/manifest.json"))
    parser.add_argument("--questions", type=Path, default=Path("evaluation/nist-v1/questions.json"))
    parser.add_argument("--sources", type=Path, default=Path("data/benchmarks/nist-v1"))
    parser.add_argument("--download", action="store_true", help="Download missing pinned sources")
    parser.add_argument(
        "--validate-only", action="store_true", help="Verify PDFs and labels without API calls"
    )
    parser.add_argument("--base-url", default="http://127.0.0.1:8000")
    parser.add_argument(
        "--output", type=Path, default=Path("evaluation/nist-v1/retrieval-fastembed.json")
    )
    parser.add_argument("--state", type=Path, default=Path(".cache/nist-v1-run.json"))
    parser.add_argument("--ingestion-timeout", type=int, default=1800)
    parser.add_argument("--allow-paid", action="store_true")
    parser.add_argument(
        "--cleanup", action="store_true", help="Delete only documents newly created by this run"
    )
    args = parser.parse_args()
    sources, questions = load_dataset(args.manifest, args.questions)
    if args.download:
        download_sources(sources, args.sources)
    print(json.dumps(validate_evidence(sources, questions, args.sources)), flush=True)
    if args.validate_only:
        return
    fingerprints = {
        "manifest_sha256": digest(args.manifest),
        "questions_sha256": digest(args.questions),
        "runner_sha256": digest(Path(__file__)),
        "uv_lock_sha256": digest(Path("uv.lock")),
    }
    headers = {"X-API-Key": os.environ["ATLAS_API_KEY"]} if os.environ.get("ATLAS_API_KEY") else {}
    with httpx.Client(base_url=args.base_url.rstrip("/"), headers=headers, timeout=180) as client:
        run(
            client,
            sources,
            questions,
            args.sources,
            args.output,
            args.state,
            fingerprints,
            allow_paid=args.allow_paid,
            timeout=args.ingestion_timeout,
            cleanup=args.cleanup,
        )
    print(f"Saved {args.output}", flush=True)


if __name__ == "__main__":
    main()
