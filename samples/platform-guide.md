# Document Atlas — Platform Guide

## Ingestion pipeline

The document ingestion pipeline accepts PDF, UTF-8 text, and Markdown files. The API writes each upload to disk and records a queued job in the relational database. A dedicated worker parses the source, preserves PDF page numbers, splits the text into overlapping chunks, generates embeddings, and indexes them in ChromaDB. SQL metadata becomes ready only after vector indexing succeeds.

Content hashes detect duplicate uploads. Queued jobs survive application restarts. Jobs interrupted during processing are queued again when the application starts. A failed job retains an actionable error message and can be retried with the reindex endpoint.

## Citation handling

Every retrieved passage has a chunk identifier, source filename, and page number. The generation service supplies numbered passages to the language model. Answers refer to these passages using labels such as [1] and [2]. The server checks that cited labels exist in the current retrieval context. A valid citation label does not prove that the passage supports the claim; human review or a separate entailment evaluator is still required.

## Conversation memory

Completed user questions and assistant answers are stored in SQL. The most recent six turns are supplied as bounded conversation history. Short follow-up questions are expanded with the previous user question to improve retrieval. Each response exposes its retrieval query for inspection. Failed or disconnected partial answers are not stored as completed conversation turns.

## Streaming

The chat streaming endpoint uses Server-Sent Events over a POST request. A sources event introduces the retrieved evidence, delta events contain answer text, and a done event contains the completed response and citation results. An error event signals failure. Clients should treat a stream without a done event as incomplete.
