import hashlib
import json

import httpx
import pytest
from pydantic import ValidationError

from app.benchmark import (
    Question,
    Source,
    canonical,
    choose_mode,
    download_sources,
    load_dataset,
    matching_rank,
    run,
    summarize,
    validate_evidence,
    verify_file,
)


def question(split="dev", answerable=True):
    return Question(
        id=f"{split}-{'positive' if answerable else 'negative'}",
        split=split,
        category="fact" if answerable else "unanswerable",
        topic_document_id=split,
        query=f"What does the {split} manual say about {'solar panels' if answerable else 'ZetaMissingCorp'}?",
        answerable=answerable,
        expected_answer="Panels convert sunlight into electricity."
        if answerable
        else "Not provided.",
        evidence=[{"document_id": split, "page": 2, "quote": "convert sunlight"}]
        if answerable
        else [],
        reason=None if answerable else "Company is absent.",
        absent_term=None if answerable else "ZetaMissingCorp",
    )


def source(key, content=b"%PDF- fake"):
    return Source(
        id=key,
        split=key,
        filename=f"{key}.pdf",
        title=key,
        url=f"https://nvlpubs.nist.gov/{key}.pdf",
        page_count=2,
        size_bytes=len(content),
        sha256=hashlib.sha256(content).hexdigest(),
    )


def test_alternative_evidence_and_physical_page_metrics():
    q = question()
    q.evidence.append(q.evidence[0].model_copy(update={"page": 3}))
    noise = {"document_id": "other", "page": 2, "text": "convert sunlight"}
    relevant = {"document_id": "dev", "page": 3, "text": "Panels convert\n sunlight to power"}
    assert matching_rank([noise, relevant, relevant], q) == 2
    assert matching_rank([noise] * 5 + [relevant], q) is None
    assert matching_rank([relevant], question(answerable=False)) is None
    rows = [
        {
            "id": "positive",
            "answerable": True,
            "first_evidence_rank": 2,
            "latency_ms": 1,
            "retrieved": [noise, relevant],
        },
        {
            "id": "negative",
            "answerable": False,
            "first_evidence_rank": None,
            "latency_ms": 3,
            "retrieved": [noise],
        },
    ]
    metrics = summarize(rows)
    assert metrics["evidence_hit_at_1"] == 0
    assert metrics["evidence_hit_at_3"] == metrics["evidence_hit_at_5"] == 1
    assert metrics["mrr_at_5"] == 0.5
    assert metrics["negative_empty_result_rate"] == 0
    assert canonical("A\u00adB  \n \uff11") == "ab 1"
    with pytest.raises(ValueError):
        summarize(rows[:1])


@pytest.mark.parametrize(
    "change",
    [
        {"evidence": []},
        {"answerable": "true"},
        {"category": "unanswerable"},
    ],
)
def test_invalid_judgments_rejected(change):
    with pytest.raises(ValidationError):
        Question.model_validate({**question().model_dump(), **change})


@pytest.mark.parametrize(
    "change",
    [
        {"filename": "../outside.pdf"},
        {"filename": ".hidden.pdf"},
        {"url": "http://nvlpubs.nist.gov/file.pdf"},
        {"url": "https://nvlpubs.nist.gov@evil.example/file.pdf"},
        {"sha256": "unpinned"},
    ],
)
def test_sources_are_pinned_and_paths_and_hosts_are_bounded(change):
    with pytest.raises(ValidationError):
        Source.model_validate({**source("dev").model_dump(), **change})


def test_checksum_validation_and_no_silent_redownload(tmp_path):
    s = source("dev")
    path = tmp_path / s.filename
    path.write_bytes(b"wrong bytes")
    with pytest.raises(ValueError, match="checksum"):
        verify_file(path, s)
    with pytest.raises(ValueError, match="checksum"):
        download_sources([s], tmp_path)
    path.write_bytes(b"%PDF- fake")
    verify_file(path, s)


@pytest.mark.parametrize("kind", ["valid", "corrupt", "oversized", "redirect"])
def test_download_verifies_before_install_and_removes_partial_files(tmp_path, monkeypatch, kind):
    from app import benchmark

    content = b"%PDF- fake"
    s = source("dev", content)
    constructor = httpx.Client

    def handler(request):
        assert request.url.host == "nvlpubs.nist.gov"
        if kind == "redirect":
            return httpx.Response(302, headers={"Location": "https://other.example/data.pdf"})
        return httpx.Response(
            200,
            content={"valid": content, "corrupt": b"%PDF- fail", "oversized": content + b"extra"}[
                kind
            ],
        )

    monkeypatch.setattr(
        benchmark.httpx,
        "Client",
        lambda **kwargs: constructor(**kwargs, transport=httpx.MockTransport(handler)),
    )
    if kind == "valid":
        download_sources([s], tmp_path)
        assert (tmp_path / s.filename).read_bytes() == content
    else:
        with pytest.raises((ValueError, httpx.HTTPStatusError)):
            download_sources([s], tmp_path)
        assert not (tmp_path / s.filename).exists()
    assert not list(tmp_path.glob("*.part"))


def test_versioned_dataset_split_is_enforced(tmp_path):
    manifest = {
        "name": "sample",
        "version": 1,
        "documents": [source(s).model_dump() for s in ("dev", "test")],
    }
    dataset = {
        "name": "sample",
        "version": 1,
        "questions": [question(s, a).model_dump() for s in ("dev", "test") for a in (True, False)],
    }
    m, q = tmp_path / "manifest.json", tmp_path / "questions.json"
    m.write_text(json.dumps(manifest))
    q.write_text(json.dumps(dataset))
    assert len(load_dataset(m, q)[1]) == 4
    dataset["questions"][2]["evidence"][0]["document_id"] = "dev"
    q.write_text(json.dumps(dataset))
    with pytest.raises(ValueError, match="cross-split"):
        load_dataset(m, q)


def test_actual_pdf_page_quotes_are_validated(tmp_path):
    from scripts.verify_deployment import pdf_probe

    content = pdf_probe("benchmark-page-check")
    s = source("dev", content)
    (tmp_path / s.filename).write_bytes(content)
    q = question().model_copy(deep=True)
    q.evidence[0].quote = "keeps audit records for 73 days"
    assert validate_evidence([s], [q, question(answerable=False)], tmp_path)["physical_pages"] == 2
    q.evidence[0].page = 1
    with pytest.raises(ValueError, match="labeled PDF page"):
        validate_evidence([s], [q], tmp_path)


def test_pipeline_freezes_dev_selection_and_preserves_preexisting_documents(tmp_path, client):
    config = client.get("/api/config").json()
    assert config["retrieval"]["min_vector_similarity"] == 0.2
    assert config["chunk_words"] == 180
    output, state = tmp_path / "results.json", tmp_path / "state.json"
    ids = {"dev": "existing-id", "test": "new-id"}
    uploaded, deleted, calls = [], [], []

    def handler(request):
        path = request.url.path
        if path == "/api/config":
            return httpx.Response(200, json=config)
        if path == "/api/documents" and request.method == "GET":
            return httpx.Response(200, json=[{"id": ids["dev"]}])
        if path == "/api/documents" and request.method == "POST":
            key = ("dev", "test")[len(uploaded)]
            uploaded.append(key)
            return httpx.Response(
                202,
                json={
                    "id": ids[key],
                    "status": "ready",
                    "page_count": 2,
                    "chunk_count": 1,
                    "index_signature": config["index_signature"],
                },
            )
        if path.endswith("/chunks"):
            return httpx.Response(
                200,
                json=[
                    {
                        "id": "chunk",
                        "page": 2,
                        "ordinal": 0,
                        "text": "Panels convert sunlight into electricity.",
                    }
                ],
            )
        if path == "/api/search":
            body = json.loads(request.content)
            assert set(body["document_ids"]) == set(ids.values())
            assert body["top_k"] == 5
            split = "test" if "test manual" in body["query"] else "dev"
            if split == "test":
                selection = json.loads(output.with_suffix(".selection.json").read_text())
                assert selection["selected_mode"] == "vector"
            calls.append((split, body["mode"]))
            hit = {
                "document_id": ids[split],
                "page": 2,
                "score": 0.01,
                "text": "Panels convert sunlight into electricity.",
            }
            # Vector wins dev. Keyword wins test, which must never change the selection.
            supported = body["mode"] == ("vector" if split == "dev" else "keyword")
            if not supported:
                hit["page"] = 1
            return httpx.Response(200, json=[hit])
        if request.method == "DELETE":
            deleted.append(path.rsplit("/", 1)[1])
            return httpx.Response(204)
        raise AssertionError(f"Unexpected request: {request}")

    for key in ids:
        (tmp_path / f"{key}.pdf").write_bytes(b"%PDF- fake")
    with httpx.Client(
        base_url="http://testserver", transport=httpx.MockTransport(handler)
    ) as remote:
        report = run(
            remote,
            [source(s) for s in ids],
            [question(s, a) for s in ids for a in (True, False)],
            tmp_path,
            output,
            state,
            {"test": "fingerprint"},
            cleanup=True,
        )
        with pytest.raises(FileExistsError):
            run(remote, [], [], tmp_path, output, state, {})
    assert report["status"] == "complete" and report["selected_mode"] == "vector"
    assert report["index_coverage"]["questions_without_an_intact_evidence_anchor"] == []
    assert deleted == ["new-id"]
    assert [split for split, _ in calls] == ["dev"] * 6 + ["test"] * 6
    assert (
        choose_mode({m: {"summary": {"mrr_at_5": 1}} for m in ("keyword", "vector", "hybrid")})
        == "keyword"
    )
