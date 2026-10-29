import pytest

from app.evaluation import evaluate, measure


def test_rank_metrics_and_duplicate_results():
    labels = [{"filename": "a.md", "contains": "solar"}, {"filename": "b.md", "page": 2}]
    hits = [
        {"filename": "other.md", "text": "noise", "page": 1},
        {"filename": "a.md", "text": "solar energy", "page": 1},
        {"filename": "a.md", "text": "solar again", "page": 1},
    ]
    metrics = measure(hits, labels, 3)
    assert metrics["recall_at_k"] == 0.5
    assert metrics["reciprocal_rank"] == 0.5
    assert metrics["precision_at_k"] == pytest.approx(1 / 3)
    assert metrics["ndcg_at_k"] < 0.5
    assert measure([], labels, 5)["hit_at_k"] == 0
    with pytest.raises(ValueError):
        measure([], [], 5)


def test_real_retrieval_evaluation(client, ingest):
    ingest("solar.txt", "Solar panels convert sunlight into electricity.")
    report = evaluate(
        client,
        [
            {
                "query": "solar electricity",
                "relevant": [{"filename": "solar.txt", "contains": "sunlight"}],
            }
        ],
        modes=("keyword", "hybrid"),
    )
    assert report["modes"]["hybrid"]["summary"]["recall_at_k"] == 1
    assert report["configuration"]["embedding_provider"] == "hash"
