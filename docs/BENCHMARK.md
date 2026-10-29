# Real-document retrieval benchmark

`evaluation/nist-v1` is a versioned benchmark over ten public NIST technical PDFs, totaling 515 physical pages. It complements the original three-document synthetic smoke check. The source PDFs are fetched into ignored `data/benchmarks/nist-v1`; the repository contains source URLs, SHA-256 checksums, questions, evidence anchors, the protocol, and measured results.

These fixed historical publication versions are a retrieval corpus, not current security guidance. NIST describes its federal publications as generally public domain in the United States in its [library permissions FAQ](https://www.nist.gov/nist-research-library/library-faqs). Preserve each publication's notices. Attribution: Reprinted courtesy of the National Institute of Standards and Technology, U.S. Department of Commerce. Not copyrightable in the United States.

## Labels and split

There are 60 questions: 50 answerable facts and 10 unanswerable questions. Answerable questions mix direct questions and paraphrases. Each has an expected answer and at least one short evidence quote on a one-based **physical PDF page**, counting covers and front matter. Text extraction and representative rendered pages were inspected before searches were run. Extraction artifacts, such as spaces inside words, are preserved in the quote labels.

Questions were authored by the coding agent from source text. They have not been independently adjudicated by human reviewers. The judgments are deliberately described as evidence anchors, not exhaustive relevance labels. Repeated occurrences of the same quote in its source are accepted alternatives. Another correct passage or document can still be unjudged, so a miss can reflect incomplete labeling as well as retrieval failure. Hits do not establish complete coverage of every clause in the expected answer.

Development questions concern SHA-2, cloud definitions, and zero trust: 18 questions, including three negatives. Test questions concern the other seven documents: 42 questions, including seven negatives. All ten documents remain search candidates in both splits. This is a document-disjoint **question** split; candidate documents are not hidden during development. The test questions are held out from mode selection, not from the annotating agent, source authoring, or model pretraining. These well-known publications may occur in model training data.

Unanswerable questions request specific measurements, account details, or organizational information absent from the corpus. Each records a reason and an absent identifier checked across all extracted pages. Identifier absence is a useful construction check, not a general automatic proof of unanswerability. These negatives are relatively easy because most introduce unseen entities; they do not cover all difficult refusal cases.

## Frozen procedure

1. Validate all source hashes, PDF page counts, exact evidence quotes, unique queries, and document-disjoint question splits before uploading anything.
2. Record `protocol.json` before any retrieval, including dataset fingerprints and the deployed configuration. Keep the existing semantic chunking and retrieval parameters; do not tune thresholds after inspecting test results.
3. Upload or reuse the ten pinned sources. Scope every search to their returned document IDs, excluding other workspace documents. Verify index signatures and measure whether evidence anchors survived chunking intact.
4. Run keyword, vector, and hybrid retrieval at five results per query on development questions. Select the highest development MRR@5, with fixed tie priority keyword, vector, then hybrid.
5. Write the selection and its checksum **before issuing any test query**. Run all three predeclared modes on the test questions as descriptive comparisons. Report the selected mode's test result without selecting again on test scores.
6. Preserve per-question ranks, retrieved text, source pages, component scores, misses, fingerprints, configuration, and timings. Do not silently overwrite an existing run.

`app.benchmark` implements the procedure. The config API includes non-secret chunk/retrieval settings and implementation fingerprints, which the runner compares with its local source before running. It refuses paid embeddings unless `--allow-paid` is provided. It never calls a generation API. `uv.lock` and the runner are fingerprinted in each report.

The runner retains uploaded documents by default so they can be explored in the UI. `--cleanup` removes only IDs not present before that run's uploads; reused documents are preserved. Use a quiet, single-user workspace during uploads to avoid concurrent changes to document ownership or the candidate corpus. Local run IDs are written to an ignored state file for recovery. A timed-out or failed run does not silently delete documents by default.

## Metrics

| Field | Meaning |
| --- | --- |
| Evidence hit@1 / @3 / @5 | Fraction of **answerable questions** with at least one labeled evidence anchor in the first k results |
| MRR@5 | Mean reciprocal rank of the first anchor hit; zero if absent from five results |
| Negative empty-result rate | Fraction of **unanswerable questions** returning no passages; a retrieval diagnostic only |
| Median / p95 latency | Sequential end-to-end search observations, including HTTP and local contention; not concurrent load-test measurements |
| Index coverage | Questions for which at least one anchor occurs intact in a stored chunk, independently of ranking |

A match requires the stable manifest document ID, physical page, and quote after Unicode NFKC normalization, soft-hyphen removal, whitespace collapse, and case folding. Alternatives are OR targets for one required fact. They are not independent items that inflate recall or gain credit repeatedly. Negatives never enter the positive hit-rate or MRR denominator.

A nonempty result for an unanswerable question can be a relevant discussion of the topic. It is not evidence of hallucination. Correct refusal and claim-to-citation support require evaluation of generated answers. Likewise, valid citation labels alone do not prove entailment. This retrieval benchmark makes no answer-accuracy or entailment claim.

## Reproduce

The completed [reference results](../evaluation/nist-v1/RESULTS.md) include all three modes and every test miss. Hybrid was selected by development MRR@5 (0.6689); its test evidence hit@5 is 27/35 (77.1%) and MRR@5 is 0.4733. Keyword test hit@5 is 28/35 (80.0%) and MRR@5 is 0.6105; vector test hit@5 is 19/35 (54.3%) and MRR@5 is 0.3552. Selection was not changed after test inspection. The [qualitative miss review](../evaluation/nist-v1/RETRIEVAL_REVIEW.md) preserves the distinction between ranking problems and incomplete judgments.

All 50 answerable anchors occur intact in the 1,226 stored chunks. All ten negative queries return some passages in every mode; this says nothing by itself about refusal correctness. The exact model-cache artifact hashes, runtime versions, and API image ID are in `evaluation/nist-v1/environment.json`. Local regression evidence is in `evaluation/nist-v1/test-summary.json`.

Install the project and start a local API as described in README.md. For semantic retrieval use FastEmbed and semantic chunking. A fresh default API uses hash embeddings, which is a separate lexical demonstration baseline. This prepared Docker instance is on port 8002; a fresh Compose setup defaults to 8000.

```powershell
uv run python -m app.benchmark --download --validate-only
uv run python -m app.benchmark --base-url http://127.0.0.1:8002
```

Downloading is optional when the pinned files already exist. A mismatched existing file causes an error rather than accepting new contents or silently refreshing the benchmark. Downloads require HTTPS from the manifest's official NIST host, forbid redirects, and verify the pinned size and checksum before installation.

To record another run, supply **new** output and state paths:

```powershell
uv run python -m app.benchmark --base-url http://127.0.0.1:8002 --output evaluation/nist-v1/repeat.json --state .cache/nist-repeat.json
```

Set `ATLAS_API_KEY` in the command's environment if your server requires it. Do not put credentials in reports. Each full run contains 180 sequential searches and may take several minutes to ingest the PDFs on CPU. Model/index approximations, new chunk UUIDs used for ranking ties, platform extraction differences, and local contention can change repeated results or timings. The exact pinned first-run artifacts are the reference observations.

## Review and extend

Inspect every missed question against its retrieved passages and labeled PDF pages. Track separately: incomplete judgments, extraction artifacts, chunk boundaries, candidate recall, and ranking. Fixes motivated by this test set must be evaluated on a new held-out version before claiming an unbiased improvement. Independently review the labels and add more domains, cross-document questions, complex tables, and harder unanswerable questions for a stronger evaluation.

For generated-answer review, retain the actual response and citation snapshots, compare each factual claim with the cited text and expected answer, and record supported, unsupported, contradicted, or insufficient evidence. Assess refusal separately on negatives. An automated substring or citation-label check should remain a diagnostic rather than being renamed an entailment score.

A [predeclared 14-response sample](../evaluation/nist-v1/ANSWER_REVIEW.md) has now been captured through actual local Ollama SSE generation using the unchanged prompts. It exposes false extra claims, incorrect answers, and mismatched cited passages despite structural citation status being valid. All generated audit conversations were deleted after capture; the corpus remains. Its manual review was performed by the coding agent and needs independent human review before use as a formal quality score. Reproduction with the original run state is `python scripts/audit_benchmark_answers.py`; the script preserves the first capture instead of overwriting it. Review JSON is separate from the untouched raw responses.
