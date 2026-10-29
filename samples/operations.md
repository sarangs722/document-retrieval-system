# Operations Handbook

## Deployment

The Docker Compose setup runs FastAPI, PostgreSQL, and a ChromaDB server. Named volumes preserve relational records, source files, and vector data across container restarts. The API binds to localhost by default. Schema migrations run before the application starts serving requests. Only one API process is supported because the ingestion claim and active conversation locks are local to that process.

## Security and data handling

An optional shared API key protects data endpoints with the X-API-Key header. The project is a single-user workspace, not a multi-tenant service. It has no user accounts or per-document access controls. Uploaded documents are untrusted input. Prompt instructions tell the model to treat source passages as data and to ignore instructions embedded in them, but this does not guarantee resistance to prompt injection.

Deleting a document removes its source file, SQL chunks, and vectors from the active index. Saved conversations retain citation snapshots, including source excerpts. Delete affected conversations as well if those excerpts need to be removed. Switching embedding configurations creates a new collection; old collections require explicit maintenance.

## Limits and recovery

Uploads are limited to 20 MB and PDFs to 200 pages by default. Scanned PDFs require OCR before ingestion. The application does not perform OCR or preserve complex table layouts. Keyword scoring scans a bounded corpus of at most 20,000 chunks, so large deployments need a dedicated full-text index and a distributed worker queue.

## Quality gates

Continuous integration runs linting, formatting checks, unit tests, and API integration tests. A PostgreSQL and ChromaDB service test verifies the networked deployment path. Changes to parsing, embeddings, chunking, or retrieval should be followed by a reproducible evaluation run. No latency, scale, or accuracy claim is valid without recorded measurements and the benchmark configuration.
