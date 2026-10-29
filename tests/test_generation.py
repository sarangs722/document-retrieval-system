from types import SimpleNamespace

from app.chat import followup_query
from app.generation import citation_check
from app.retrieval import bm25


def test_citation_validation_rejects_unknown_and_missing_labels():
    sources = [SimpleNamespace(label=1), SimpleNamespace(label=2)]
    assert citation_check("A claim [1].", sources)[0] == "valid"
    assert citation_check("A claim [7].", sources)[0] == "invalid"
    assert citation_check("A claim [1] [7].", sources)[0] == "invalid"
    assert citation_check("No references.", sources)[0] == "missing"
    assert citation_check("No evidence.", [])[0] == "not_applicable"
    assert citation_check("Invented [1].", [])[0] == "invalid"


def test_bm25_and_query_expansion():
    scores = bm25(
        "sunlight electricity",
        ["Solar panels convert sunlight to electricity.", "Databases store records."],
    )
    assert scores[0] > 0 and scores[1] == 0
    assert bm25("the of is", ["the of is"]) == [0.0]
    history = [
        {"role": "user", "content": "What is the ingestion pipeline?"},
        {"role": "assistant", "content": "An answer."},
    ]
    assert "ingestion pipeline" in followup_query("What about its limits?", history)
    independent = (
        "Explain the complete set of database replication techniques used by distributed systems"
    )
    assert followup_query(independent, history) == independent
    assert followup_query("How are citations validated?", history) == "How are citations validated?"


def test_extractive_answer_prefers_relevant_sentences(settings):
    import asyncio

    from app.generation import Generator

    sources = [
        SimpleNamespace(
            label=1,
            text="The document ingestion pipeline parses PDFs. Ingestion jobs generate embeddings.",
        ),
        SimpleNamespace(
            label=2,
            text="Scanned documents require OCR. Delete old conversations to remove their excerpts.",
        ),
    ]

    async def run():
        return "".join(
            [
                part
                async for part in Generator(settings).stream(
                    "Explain the document ingestion pipeline", sources, []
                )
            ]
        )

    answer = asyncio.run(run())
    assert "[1]" in answer
    assert "OCR" not in answer and "[2]" not in answer
