# Project status — 29 October 2026

## Ready now

The Docker workspace is running at http://127.0.0.1:8002 with PostgreSQL, HTTP ChromaDB, FastEmbed semantic embeddings, and actual local Qwen3 LLM generation through Ollama. It now synthesizes cited answers rather than using the extractive fallback. The sample library and ten public NIST PDFs remain indexed. API reference: http://127.0.0.1:8002/docs. No OpenAI key or paid API calls were used.

After a reboot, run these from the project directory:

```powershell
.\scripts\start-ollama.ps1 -PullModel
docker compose up -d
```

The model is already downloaded; the helper skips an installed model. This prepared service stores it under ignored data/ollama, binds to localhost, disables cloud features, and limits parallel/model loading. An already running Ollama service is reused with its own settings. Local model loading can take time after inactivity. Normal Compose exposes only the API on port 8002; PostgreSQL and Chroma stay private. Native development via scripts/start.ps1 uses separate SQLite/embedded-Chroma storage.

Source control update, 30 October 2026: the project uses branch `master` and the [document-retrieval-system repository](https://github.com/sarangs722/document-retrieval-system). Remote CI results have not yet been verified, and no public application deployment has been created. Secrets, uploads, models, environments, and caches are ignored.

## Verified

- **61 local regression tests passed**, with **87.20% Python statement coverage**, including actual cached FastEmbed inference and 17 new benchmark cases. See evaluation/nist-v1/test-summary.json. The new cases cover metric denominators and alternative anchors, pinned downloads, PDF-page evidence, split enforcement, pre-test selection, and preserving pre-existing documents. The separate service integration test was not rerun in that invocation while the live benchmark was indexing; the previous 45-test run included it successfully (evaluation/test-summary.json), and the new real-document benchmark itself ran against actual PostgreSQL/HTTP Chroma.
- A frozen real-document benchmark ran 180 searches across ten NIST PDFs, 515 physical pages, and 1,226 chunks. There are 18 development and 42 test questions, with document-disjoint question topics and all ten candidate documents in both splits. Hybrid won development MRR; its test evidence hit@5 was 27/35 (77.1%), versus keyword 28/35 (80.0%) and vector 19/35 (54.3%). These are exact-anchor observations with agent-authored, incomplete relevance judgments, not answer accuracy. See evaluation/nist-v1/RESULTS.md and RETRIEVAL_REVIEW.md.
- A predeclared 14-answer sample used actual Qwen3 SSE generation on test topics. Manual coding-agent review found false extra claims, a wrong SHA-3 answer, citation passages that did not support the requested claim, and a mixed refusal with an invented organizational assertion. All 14 generated conversations were deleted after capture. Actual answers, cited excerpts, model/prompt hashes, and review notes are retained under evaluation/nist-v1/. This is not independent human adjudication. See ANSWER_REVIEW.md; do not interpret citation_status=valid as a factual-support pass.
- Five real LLM probes passed through the Docker API: paraphrased retention, encryption, conversational follow-up, document filtering, and refusal of an unanswerable question despite retrieved context. Every answerable case has valid source labels; the refusal correctly contains no citation. See evaluation/local-llm-smoke.json for actual answers, checks, and timings. The synthetic records were deleted afterwards.
- A separate PDF-to-LLM probe verifies a new retention fact on page two and its page-two citation; see evaluation/local-llm-pdf-smoke.json.
- The local model identity and server version are recorded in evaluation/local-model-environment.json. The tested tag is qwen3:1.7b, with its exact digest recorded. Cold model loading can be slow. The latest short synthetic probes took roughly 0.6–1.3 seconds, while the separate PDF probe took about 8.6 seconds. These measurements are specific smoke checks, not representative latency claims.
- Docker deployment runs as a non-root user and was tested against actual PostgreSQL and networked Chroma. All three containers were recreated and source files, SQL data, vectors, and conversations persisted. See container-smoke.json, container-lifecycle.json, and container-environment.json under evaluation/ for that earlier deployment verification.
- The twelve-question sample retrieval benchmark passed against the Docker stack. It has only three documents and six chunks and is not a held-out accuracy benchmark. Earlier retrieval reports remain unchanged as evidence of their extractive-generation configuration; retrieval itself does not invoke a generator.
- Swagger/ReDoc render correctly; Swagger can execute requests and supply the workspace API key. The main workspace retains its same-origin security policy.
- Ruff lint/format and JavaScript syntax checks pass. OpenAI adapters remain covered with fake responses; a live paid OpenAI call has not been made.

A broader library explanation initially omitted citations and was flagged by the application. The prompt was tightened and the repeated library question then returned valid citation labels, including in the browser. Both observations are retained in evaluation/local-llm-library-initial.json and evaluation/local-llm-library-smoke.json; the five fact probes were rerun successfully after that prompt change. Thirteen targeted provider/context/heartbeat regression tests also passed after the change. The initial failure demonstrates that the small model can still miss citation instructions; validation flags remain visible rather than inventing references.

## Local generation behavior

The Ollama adapter forwards answer content, does not display thinking fields, bounds output, and rejects incomplete/token-truncated responses. Input packing conservatively budgets UTF-8 bytes, shortening history and evidence while retaining exact source prefixes in citation snapshots. Oversized questions fail clearly instead of relying on silent truncation. SSE keep-alive comments support slow generation and cancellation closes provider streams without saving partial turns.

The five fact probes and the PDF check are small synthetic checks. They do not establish general answer accuracy, claim entailment, prompt-injection resistance, or production performance. A structurally valid citation is not proof of support. An uncited refusal can have citation_status=missing even when it is the correct response to insufficient evidence.

## Remaining work

1. Independently review and broaden the real-document labels, then implement and evaluate claim-level support checks or evidence-sufficiency gating. The small local model currently makes unsupported claims even with valid citation labels. Preserve this reference run and use a new held-out test version for changes motivated by its failures.
2. Inspect the supplied GitHub Actions workflow after pushing to the configured repository; no remote CI success is claimed here.
3. Optionally configure OPENAI_API_KEY, ATLAS_LLM_PROVIDER=openai, and an appropriate ATLAS_LLM_MODEL to verify that alternative backend. This is no longer required for working LLM answers because the local backend is live. Never put keys into chat, source control, or reports.

Scanned PDFs still require external OCR; complex table/layout extraction is limited. The platform is single-user/single-process and has a bounded Python BM25 scan. Saved conversations retain source excerpts after document deletion, and previous index collections need separate retention handling. Public/team use needs identity, document ACLs, isolated parsing, rate limits, TLS, backups, and monitoring.

## Read next

README.md has startup/provider commands, ARCHITECTURE.md explains design and limits, DEVELOPMENT.md explains reproducible checks, BENCHMARK.md describes the frozen evaluation, and PORTFOLIO.md gives defensible resume wording. Real local LLM generation and the previous Docker/PostgreSQL blocker are both verified; the real-document audit now supplies concrete reliability failures to work on next.
