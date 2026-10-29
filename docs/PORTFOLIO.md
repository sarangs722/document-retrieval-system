# Portfolio and interview guide

## Resume wording

After understanding and demonstrating the implementation:

**Intelligent Document Retrieval Platform** — Python, FastAPI, PostgreSQL, ChromaDB, FastEmbed, Ollama, OpenAI APIs, Docker

- Built a RAG application with PDF ingestion, local semantic embeddings, hybrid BM25/vector ranking, locally generated LLM answers with page-level citations, persisted conversation history, and server-sent streaming responses.
- Implemented durable ingestion recovery, versioned vector indexes, semantic boundary chunking, and passage-level evaluation with Recall@k, MRR, and nDCG.
- Built a reproducible 60-question retrieval benchmark over ten public technical PDFs, with document-disjoint development/test questions, exact page evidence, pinned source checksums, and retained failure cases.

Check STATUS.md for current evidence. Docker deployment has been verified against actual PostgreSQL and HTTP Chroma, including persistence through container recreation; the deployment probe and runtime reports are in evaluation/. OpenAI is contract-tested with fakes and needs a credentialed check. Twelve synthetic questions do not support general accuracy, scale, or latency claims.

Actual local Qwen3 generation has also passed five synthetic fact/citation/follow-up/refusal checks through the Docker API. Explain the distinction between this live local-provider verification and the separately unverified paid OpenAI backend. Do not present these probes as held-out accuracy, citation entailment, or prompt-injection resistance measurements.

## Interview topics

1. Why SQL controls visibility while Chroma supplies vector candidates.
2. How dense retrieval handles paraphrases and BM25 preserves exact terms.
3. Why rank fusion avoids comparing incompatible score scales.
4. How queued jobs, startup recovery, and vector replacement make ingestion replay safe.
5. How signatures prevent mixing embedding spaces after model changes.
6. Why citation label validation cannot prove factual support.
7. What Python BM25, in-process parsing, and single-process locking limit.
8. How held-out evaluation separates retrieval from answer quality.
9. What identity, document ACLs, distributed leases, budgets, and monitoring add for team use.

## Five-minute demo

Upload the three samples. Ask what the ingestion pipeline does and open a citation. Ask about its failure recovery and inspect the expanded retrieval query. Select one document and switch to passage search. Run evaluation and explain both a result and the limitations of the tiny corpus.

For the larger benchmark, inspect evaluation/nist-v1/RESULTS.md and the corresponding ranked passages in its JSON report. Explain the difference between evidence-anchor hits and complete answer correctness, why negatives have a separate denominator, and why the test split was not used to choose retrieval parameters. The prepared workspace retains the ten PDFs so their source pages can be opened during the demo. [Benchmark methodology](BENCHMARK.md) documents the provenance and limitations; the labels were agent-authored and need independent review.

## Next valuable work

Independent review and broader domains for the real-document labels; claim-level citation support evaluation; layout-aware PDF/table extraction; isolated OCR; user accounts and ACLs; PostgreSQL full-text retrieval; distributed ingestion leases; measured load tests. Keep the current benchmark as a fixed reference and use a new test version to assess improvements motivated by its failures.
