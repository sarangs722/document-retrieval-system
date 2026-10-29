# Development

Use Python 3.12/3.13 and uv.lock. Install with `uv sync --frozen --python 3.12 --extra dev --extra local`. The local extra adds ONNX embeddings; dev adds tests, coverage, and Ruff. This prepared workspace has FastEmbed and Qwen3 models downloaded, with ignored .env enabling semantic retrieval and Ollama generation.

## Local language model

```powershell
.\scripts\start-ollama.ps1 -PullModel
docker compose up -d
```

The helper starts the installed Ollama binary on localhost with cloud features disabled, one loaded model, and one parallel request. Models are stored in ignored data/ollama. If a service is already running it is reused, with its existing configuration. The pull is skipped if the model is present. After a reboot start Ollama before asking generated-answer questions. The Docker API can start without it; generation requests then fail safely until it is available.

Local API execution defaults to 127.0.0.1:11434; Docker Desktop defaults to host.docker.internal:11434. Other deployments need a reachable ATLAS_OLLAMA_BASE_URL. Do not expose an unauthenticated model service publicly. The existing OpenAI provider remains independently configurable.

```powershell
uv run python scripts/verify_llm.py --base-url http://127.0.0.1:8002 --output evaluation/local-llm-smoke.json
```

This creates unique synthetic probe documents and tests real streamed answers, source filtering, follow-up memory, and refusal with retrieved but insufficient context. Probe documents/conversations are removed afterwards. Paid providers are rejected unless --allow-paid is explicitly supplied. Fact-anchor matches and citation labels are smoke checks, not entailment or real-world accuracy measurements. The output includes response text and time-to-first-token from this particular run.

ATLAS_OLLAMA_CONTEXT_TOKENS defaults to 8192 and ATLAS_OLLAMA_MAX_OUTPUT_TOKENS to 512. Conservative byte-based packing limits history and evidence; questions that cannot fit are rejected. Cold model loading can be slow. SSE keep-alive comments run every ten seconds by default and do not constitute answer tokens. `ollama stop qwen3:1.7b` unloads the model if memory is needed; normal keep-alive expiration also unloads it.

## Checks

```powershell
uv run ruff check app tests scripts
uv run ruff format --check app tests scripts
uv run pytest --cov=app --cov-report=term-missing
```

Tests use isolated SQLite databases, actual migrations, and embedded Chroma. OpenAI tests use fake streams/embeddings, never paid requests.

```powershell
uv run python scripts/prefetch_model.py
$env:ATLAS_TEST_SEMANTIC = '1'
$env:HF_HUB_OFFLINE = '1'
uv run pytest tests/test_semantic.py
```

Those variables enable the real-model test for a full-suite run too. Remove them afterwards with `Remove-Item Env:ATLAS_TEST_SEMANTIC, Env:HF_HUB_OFFLINE`.

Codex's Windows network sandbox blocks asyncio local socket pairs; tests there need an approved execution outside it. Normal local development does not. Writable scratch space can use --basetemp=.cache/my-test-run; choose a new directory with no work you need to retain.

## External services

For this project's local containers, enable the optional loopback test ports and create a dedicated database once:

```powershell
docker compose -f compose.yaml -f compose.integration.yaml up -d --wait
docker compose exec postgres createdb -U atlas atlas_test
```

If `atlas_test` already exists, reuse it. The override exposes PostgreSQL on 5433 and Chroma on 8001; normal Compose exposes neither service.

```powershell
$env:ATLAS_TEST_DATABASE_URL = 'postgresql+psycopg://atlas:atlas-local-dev@127.0.0.1:5433/atlas_test'
$env:ATLAS_TEST_CHROMA_HOST = '127.0.0.1'
$env:ATLAS_TEST_CHROMA_PORT = '8001'
uv run pytest tests/test_services.py -v
```

Use a disposable database: the test creates schema and uploads/deletes records. It skips without ATLAS_TEST_DATABASE_URL. The included CI job provisions both services, but remote CI has not run from this workspace.

The URL above uses the default development password. Substitute your configured password if changed. Set `ATLAS_TEST_SEMANTIC=1` and `HF_HUB_OFFLINE=1`, then run the entire pytest suite to include both actual model and service tests. Remove these test environment variables after use.

Return to private service networking with `docker compose up -d --wait` after testing.

## Container recreation smoke test

With the API on port 8002 in this prepared workspace:

```powershell
$env:ATLAS_LLM_PROVIDER = 'extractive'
docker compose up -d --wait
uv run python scripts/verify_deployment.py --base-url http://127.0.0.1:8002 --keep --output evaluation/container-smoke.json
docker compose up -d --force-recreate --wait --wait-timeout 180
uv run python scripts/verify_deployment.py --base-url http://127.0.0.1:8002 --resume evaluation/container-smoke.json
Remove-Item Env:ATLAS_LLM_PROVIDER
docker compose up -d --wait
```

Use port 8000 for a fresh default configuration. The probe uploads a unique two-page PDF and Markdown source, checks duplicate handling, filtering, citations, follow-ups, history, and SSE events, then retains its records. Resume verifies persisted history and vector search and reindexes the PDF to prove source-file persistence. It deletes only this run's probe records afterwards. A failed run preserves its IDs in the report for inspection. The probe requires extractive generation so it never makes paid LLM calls; choose hash or FastEmbed embeddings to avoid paid embedding calls as well.

CI includes the same build and recreation check. It is a local deployment validation, not a hosted/public deployment or a load test.

## Evaluation

```powershell
uv run python -m app.evaluation --local --mode all --output evaluation/latest.json
uv run python -m app.evaluation --base-url http://127.0.0.1:8000 --seed --mode all
```

Local evaluation creates a disposable workspace, follows .env/environment embeddings, and reuses data/models. It never calls an LLM; OpenAI embeddings still incur calls if selected. Remote --seed uploads samples and scopes queries to those IDs. Without it, queries use the existing corpus. Set ATLAS_API_KEY in the process environment if required; HTTP CLI clients do not automatically read the server's .env key.

Use base URL `http://127.0.0.1:8002` to target this prepared Docker workspace. `evaluation/container-baseline.json` records that run alongside the two local baselines.

Labels contain filename, optional one-based page, and a required passage substring. Duplicates earn no repeated relevance credit. Precision@k divides by k even if fewer results return. MRR/nDCG use one-based ranks. The present questions have one label each; use disjoint labels when extending them.

## Migrations and code map

The versioned NIST benchmark has its own [protocol and reproduction guide](BENCHMARK.md). Run `python -m app.benchmark --download --validate-only` to verify its pinned PDFs and page evidence without touching a server. Full runs preserve the corpus by default, scope queries to its ten IDs, and write selection before any held-out test query. `python scripts/report_benchmark.py` renders the completed JSON report as Markdown. The normal CI suite exercises benchmark metrics, source validation, downloader checks, PDF-page evidence validation, selection ordering, and cleanup with local fixtures; it does not redownload this public corpus or make paid calls on every push.

On Windows, if the shared pytest temporary directory has an access error, set `--basetemp .cache/pytest-<new-run-name>` to use a fresh workspace directory. Do not delete a shared temporary directory to work around it.

`python -m app.migrate` upgrades schema. Startup migrates unless ATLAS_AUTO_MIGRATE=false. Review SQLite/PostgreSQL compatibility and back up before destructive changes. Use one API process; --reload is for development only.

| Module | Responsibility |
| --- | --- |
| main | API, lifespan, auth, upload limits, UI |
| db and migrations | Schema and history |
| ingestion | Durable queue and recovery |
| parsing | Extraction and page-preserving chunks |
| embeddings and vectors | Providers and Chroma |
| retrieval | BM25, vectors, fusion, duplicates |
| generation and chat | Answers, memory, citations, streaming |
| evaluation | Labels, metrics, baseline reporting |
| benchmark | Pinned real PDFs, question splits, dev selection, evidence-anchor evaluation |
| static | UI with no frontend build step |
