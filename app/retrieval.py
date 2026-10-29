import math
from collections import Counter

from sqlalchemy import select

from app.db import Chunk, Document
from app.embeddings import tokens
from app.schemas import SearchHit

BM25_K1 = 1.5
BM25_B = 0.75
RRF_CONSTANT = 60
CANDIDATE_MULTIPLIER = 6
CANDIDATE_CAP = 72
DUPLICATE_JACCARD = 0.85
DEFAULT_VECTOR_THRESHOLDS = {"hash": 0.2, "fastembed": 0.6, "openai": 0.3}


def retrieval_configuration(settings):
    return {
        "bm25_k1": BM25_K1,
        "bm25_b": BM25_B,
        "rrf_constant": RRF_CONSTANT,
        "candidate_multiplier": CANDIDATE_MULTIPLIER,
        "candidate_cap": CANDIDATE_CAP,
        "duplicate_jaccard": DUPLICATE_JACCARD,
        "min_vector_similarity": (
            settings.min_vector_similarity
            if settings.min_vector_similarity is not None
            else DEFAULT_VECTOR_THRESHOLDS[settings.embedding_provider]
        ),
        "max_corpus_chunks": settings.max_corpus_chunks,
    }


def bm25(query: str, texts: list[str]) -> list[float]:
    """Okapi BM25 with positive IDF, including for very small corpora."""
    corpus = [Counter(tokens(text)) for text in texts]
    lengths = [sum(row.values()) for row in corpus]
    average = sum(lengths) / len(lengths) if lengths else 1
    terms = set(tokens(query))
    frequencies = {term: sum(term in row for row in corpus) for term in terms}
    scores = []
    for row, length in zip(corpus, lengths, strict=True):
        score = 0.0
        for term in terms:
            tf = row[term]
            idf = math.log(1 + (len(corpus) - frequencies[term] + 0.5) / (frequencies[term] + 0.5))
            score += (
                idf
                * tf
                * (BM25_K1 + 1)
                / (tf + BM25_K1 * (1 - BM25_B + BM25_B * length / (average or 1)))
            )
        scores.append(score)
    return scores


class Retriever:
    def __init__(self, sessions, embedder, vectors, settings):
        self.sessions, self.embedder, self.vectors, self.settings = (
            sessions,
            embedder,
            vectors,
            settings,
        )

    def search(self, query: str, top_k=5, document_ids=None, mode="hybrid") -> list[SearchHit]:
        # SQL is authoritative for visibility; uncommitted/stale vectors can never leak into results.
        with self.sessions() as session:
            statement = (
                select(Chunk, Document.filename)
                .join(Document)
                .where(
                    Document.status == "ready", Document.index_signature == self.embedder.signature
                )
                .order_by(Chunk.id)
                .limit(self.settings.max_corpus_chunks)
            )
            if document_ids:
                statement = statement.where(Chunk.document_id.in_(document_ids))
            rows = session.execute(statement).all()
        if not rows:
            return []
        by_id = {chunk.id: (chunk, filename) for chunk, filename in rows}
        keyword_scores = dict(
            zip(by_id, bm25(query, [chunk.text for chunk, _ in rows]), strict=True)
        )
        limit = min(top_k * CANDIDATE_MULTIPLIER, CANDIDATE_CAP)
        keyword = sorted(
            ((key, score) for key, score in keyword_scores.items() if score > 0),
            key=lambda pair: (-pair[1], pair[0]),
        )[:limit]
        vector = []
        if mode != "keyword":
            embedding = self.embedder.encode([query], query=True)[0]
            # Filter vectors by the ready SQL documents before ANN search, not afterwards only.
            eligible = sorted({chunk.document_id for chunk, _ in rows})
            threshold = self.settings.min_vector_similarity
            if threshold is None:
                threshold = DEFAULT_VECTOR_THRESHOLDS[self.settings.embedding_provider]
            vector = [
                (key, score)
                for key, score in self.vectors.search(embedding, limit, eligible)
                if key in by_id
                and score > threshold
                and (self.settings.embedding_provider != "hash" or keyword_scores[key] > 0)
            ]
        rankings = (
            [vector] if mode == "vector" else [keyword] if mode == "keyword" else [vector, keyword]
        )
        fused: dict[str, float] = {}
        for ranking in rankings:
            for rank, (key, _) in enumerate(ranking, 1):
                fused[key] = fused.get(key, 0) + 1 / (RRF_CONSTANT + rank)
        vector_scores = dict(vector)
        results = []
        # Suppress near-duplicate overlap chunks while retaining distinct evidence.
        seen: list[set[str]] = []
        for key in sorted(fused, key=lambda item: (-fused[item], item)):
            chunk, filename = by_id[key]
            terms = set(tokens(chunk.text))
            if any(len(terms & old) / max(1, len(terms | old)) > DUPLICATE_JACCARD for old in seen):
                continue
            seen.append(terms)
            results.append(
                SearchHit(
                    chunk_id=key,
                    document_id=chunk.document_id,
                    filename=filename,
                    page=chunk.page,
                    text=chunk.text,
                    score=round(fused[key], 6),
                    vector_score=vector_scores.get(key),
                    keyword_score=keyword_scores.get(key),
                )
            )
            if len(results) == top_k:
                break
        return results
