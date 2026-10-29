import chromadb
from chromadb.config import Settings as ChromaSettings

from app.config import Settings


class VectorIndex:
    def __init__(self, settings: Settings, signature: str):
        config = ChromaSettings(anonymized_telemetry=False)
        if settings.chroma_host:
            self.client = chromadb.HttpClient(
                host=settings.chroma_host, port=settings.chroma_port, settings=config
            )
        else:
            self.client = chromadb.PersistentClient(
                path=str(settings.data_dir / "chroma"), settings=config
            )
        self.collection = self.client.get_or_create_collection(
            name=f"atlas-{signature}",
            embedding_function=None,
            metadata={"hnsw:space": "cosine"},
        )

    def upsert(self, chunks, embeddings):
        for offset in range(0, len(chunks), 128):
            batch = chunks[offset : offset + 128]
            self.collection.upsert(
                ids=[chunk.id for chunk in batch],
                embeddings=embeddings[offset : offset + 128],
                metadatas=[
                    {"document_id": chunk.document_id, "page": chunk.page} for chunk in batch
                ],
            )

    def delete_document(self, document_id: str):
        self.collection.delete(where={"document_id": document_id})

    def close(self):
        self.client.close()

    def search(self, embedding, limit: int, document_ids: list[str] | None = None):
        count = self.collection.count()
        if not count:
            return []
        kwargs = {}
        if document_ids:
            kwargs["where"] = {"document_id": {"$in": document_ids}}
        data = self.collection.query(
            query_embeddings=[embedding],
            n_results=min(limit, count),
            include=["distances"],
            **kwargs,
        )
        return [
            (key, 1 - distance)
            for key, distance in zip(data["ids"][0], data["distances"][0], strict=True)
        ]
