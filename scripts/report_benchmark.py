"""Render the frozen retrieval report as a compact Markdown artifact."""

import argparse
import hashlib
import json
from pathlib import Path


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--report", type=Path, default=Path("evaluation/nist-v1/retrieval-fastembed.json")
    )
    parser.add_argument("--questions", type=Path, default=Path("evaluation/nist-v1/questions.json"))
    parser.add_argument("--output", type=Path, default=Path("evaluation/nist-v1/RESULTS.md"))
    args = parser.parse_args()
    report = json.loads(args.report.read_text(encoding="utf-8"))
    if report["status"] != "complete":
        raise ValueError("Cannot summarize an incomplete benchmark")
    if (
        hashlib.sha256(args.questions.read_bytes()).hexdigest()
        != report["fingerprints"]["questions_sha256"]
    ):
        raise ValueError("Question file differs from the evaluated version")
    questions = {
        q["id"]: q for q in json.loads(args.questions.read_text(encoding="utf-8"))["questions"]
    }
    lines = [
        "# NIST technical corpus: reference retrieval run",
        "",
        f"Completed: {report['finished_at']}. Selected on development: **{report['selected_mode']}**.",
        "",
        "Ten PDFs, 515 physical pages, 60 agent-authored questions. Development: 15 answerable + 3 unanswerable; test: 35 answerable + 7 unanswerable. All ten documents are retrieval candidates in both splits.",
        "",
        "These are exact evidence-anchor hit rates, not generated-answer accuracy. Labels are not exhaustive or independently human-adjudicated. See ../../docs/BENCHMARK.md for protocol, provenance, and limitations.",
        "",
        "| Split | Mode | Evidence hit@1 | Hit@3 | Hit@5 | MRR@5 | Negative empty results | Median / p95 ms |",
        "| --- | --- | ---: | ---: | ---: | ---: | ---: | ---: |",
    ]
    for split in ("dev", "test"):
        for mode, row in report[split].items():
            s = row["summary"]
            lines.append(
                f"| {split} | {mode} | {s['evidence_hit_at_1']:.1%} | {s['evidence_hit_at_3']:.1%} | {s['evidence_hit_at_5']:.1%} | {s['mrr_at_5']:.4f} | {s['negative_empty_result_rate']:.1%} | {s['median_latency_ms']:.0f} / {s['p95_latency_ms']:.0f} |"
            )
    coverage = report["index_coverage"]
    lines.extend(
        [
            "",
            "A nonempty search result for an unanswerable question is only a retrieval observation; no refusal or hallucination rate was measured.",
            "",
            f"The indexed corpus contains {coverage['chunks']} chunks. Questions without an intact anchor in any stored chunk: {', '.join(coverage['questions_without_an_intact_evidence_anchor']) or 'none'}.",
            "",
            "## Test misses by mode",
            "",
            "All missed answerable questions are retained below, including misses attributable to incomplete labels or chunking. Full ranked passages and component scores are in the JSON report.",
            "",
            "| Mode | Question | Expected evidence pages | First returned document/page |",
            "| --- | --- | --- | --- |",
        ]
    )
    for mode, result in report["test"].items():
        for row in result["questions"]:
            if not row["answerable"] or row["first_evidence_rank"] is not None:
                continue
            q = questions[row["id"]]
            labels = ", ".join(f"{e['document_id']} p{e['page']}" for e in q["evidence"])
            first = row["retrieved"][0] if row["retrieved"] else None
            first_label = f"{first['document_id']} p{first['page']}" if first else "none"
            lines.append(f"| {mode} | {q['id']} | {labels} | {first_label} |")
    lines.extend(
        [
            "",
            "## Reproducibility",
            "",
            f"- Questions SHA-256: `{report['fingerprints']['questions_sha256']}`",
            f"- Manifest SHA-256: `{report['fingerprints']['manifest_sha256']}`",
            f"- Pre-test selection SHA-256: `{report['selection_sha256']}`",
            f"- Embeddings: {report['configuration']['embedding_model']}; chunking: {report['configuration']['chunk_strategy']}.",
            "- Source/model/chunk/retrieval settings, implementation hashes, and runner/lockfile hashes are recorded in the JSON artifacts.",
            "- Sequential local timings include HTTP overhead and machine contention. This is not a load test.",
            "- No generator or paid API was invoked for this run.",
            "",
        ]
    )
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text("\n".join(lines), encoding="utf-8")
    print(args.output)


if __name__ == "__main__":
    main()
