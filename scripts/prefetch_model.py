"""Download and sanity-check the optional local semantic model once."""

import os

os.environ.setdefault("ATLAS_EMBEDDING_PROVIDER", "fastembed")
os.environ.setdefault("ATLAS_CHUNK_STRATEGY", "semantic")

from app.config import Settings
from app.embeddings import Embedder, cosine

settings = Settings()
embedder = Embedder(settings)
vectors = embedder.encode(
    [
        "A physician treats a patient.",
        "A doctor provides medical care.",
        "A database stores records.",
    ]
)
print(f"Model: {settings.embedding_model}; dimensions: {len(vectors[0])}")
print(f"Related meaning: {cosine(vectors[0], vectors[1]):.3f}")
print(f"Unrelated meaning: {cosine(vectors[0], vectors[2]):.3f}")
