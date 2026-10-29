# Retrieval Design Notes

## Hybrid retrieval

Hybrid retrieval combines dense vector search with BM25 keyword search. Vector search can find passages with similar meaning when genuine semantic embeddings are enabled. BM25 rewards exact terms and is useful for product names, identifiers, and technical vocabulary. Reciprocal rank fusion merges the candidate rankings using a rank constant of 60. Fusion scores are ranking signals, not calibrated confidence probabilities.

## Chunking strategy

Sentence chunking packs sentences into page-preserving passages with a hard word limit and overlap. Semantic chunking also embeds adjacent sentence units and starts a new passage when their cosine similarity falls below a threshold. Overlap retains context at boundaries. Chunk size, overlap, model name, and strategy are included in the index signature so incompatible indexes are not mixed.

## Embedding choices

The default hash embedding provider is a deterministic lexical demonstration. It requires no model download and no API credentials. Hash embeddings do not understand synonyms. FastEmbed runs a real ONNX embedding model locally on the CPU. OpenAI embeddings are available through a separate configurable provider and send document text to the API.

## Retrieval evaluation

Retrieval evaluation uses a labeled set of questions and relevant passages. Recall at k measures how many relevant labels appear in the first k results. Mean reciprocal rank measures how early the first relevant result appears. Normalized discounted cumulative gain rewards relevant results near the top of the ranking. Benchmark scores on a tiny synthetic corpus are smoke checks and should not be presented as general real-world retrieval accuracy.
