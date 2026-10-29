"""Explicit embedding providers; the hash provider is a lexical demo, not an AI model."""

import hashlib
import math
import re
from functools import cached_property

from app.config import Settings

STOPWORDS = frozenset(
    [
        "a",
        "an",
        "and",
        "are",
        "as",
        "at",
        "be",
        "been",
        "by",
        "can",
        "do",
        "does",
        "for",
        "from",
        "had",
        "has",
        "have",
        "how",
        "i",
        "in",
        "into",
        "is",
        "it",
        "its",
        "me",
        "my",
        "of",
        "on",
        "or",
        "our",
        "s",
        "that",
        "the",
        "their",
        "there",
        "these",
        "they",
        "this",
        "to",
        "was",
        "we",
        "were",
        "what",
        "when",
        "where",
        "which",
        "who",
        "why",
        "will",
        "with",
        "would",
        "you",
        "your",
        "about",
        "please",
        "tell",
    ]
)


def tokens(text: str) -> list[str]:
    return [word for word in re.findall(r"\w+", text.casefold()) if word not in STOPWORDS]


def cosine(a: list[float], b: list[float]) -> float:
    denom = math.sqrt(sum(v * v for v in a) * sum(v * v for v in b))
    return sum(x * y for x, y in zip(a, b, strict=True)) / denom if denom else 0.0


class Embedder:
    def __init__(self, settings: Settings):
        self.settings = settings

    @cached_property
    def signature(self) -> str:
        s = self.settings
        # Both embedding space and chunk recipe are versioned; never silently mix indexes.
        recipe = f"v1|{s.embedding_provider}|{s.embedding_model}|{s.chunk_strategy}|{s.chunk_words}|{s.chunk_overlap_words}|{s.semantic_threshold}"
        return hashlib.sha256(recipe.encode()).hexdigest()[:32]

    @cached_property
    def local_model(self):
        from fastembed import TextEmbedding

        return TextEmbedding(
            model_name=self.settings.embedding_model,
            cache_dir=str(self.settings.model_cache_dir or self.settings.data_dir / "models"),
            threads=2,
        )

    @cached_property
    def openai(self):
        from openai import OpenAI

        return OpenAI(
            api_key=self.settings.openai_api_key.get_secret_value(), timeout=60, max_retries=2
        )

    def encode(self, texts: list[str], *, query: bool = False) -> list[list[float]]:
        if not texts:
            return []
        if self.settings.embedding_provider == "fastembed":
            method = self.local_model.query_embed if query else self.local_model.passage_embed
            return [vector.tolist() for vector in method(texts, batch_size=32)]
        if self.settings.embedding_provider == "openai":
            result = []
            for offset in range(0, len(texts), 64):
                response = self.openai.embeddings.create(
                    input=texts[offset : offset + 64], model=self.settings.embedding_model
                )
                result.extend(
                    item.embedding for item in sorted(response.data, key=lambda x: x.index)
                )
            return result
        vectors = []
        for text in texts:
            vector = [0.0] * 384
            for token in tokens(text):
                digest = hashlib.blake2b(token.encode(), digest_size=8).digest()
                vector[int.from_bytes(digest, "little") % 384] += 1.0
            norm = math.sqrt(sum(value * value for value in vector)) or 1
            vectors.append([value / norm for value in vector])
        return vectors

    def close(self):
        if "openai" in self.__dict__:
            self.openai.close()
