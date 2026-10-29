from pathlib import Path
from typing import Literal

from pydantic import AnyHttpUrl, Field, SecretStr, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_prefix="ATLAS_", env_file=".env", extra="ignore")

    database_url: str = "sqlite:///./data/atlas.db"
    data_dir: Path = Path("data")
    embedding_provider: Literal["hash", "fastembed", "openai"] = "hash"
    embedding_model: str = ""
    model_cache_dir: Path | None = None
    min_vector_similarity: float | None = Field(default=None, ge=-1, le=1)
    llm_provider: Literal["extractive", "openai", "ollama"] = "extractive"
    llm_model: str = ""
    ollama_base_url: AnyHttpUrl = "http://127.0.0.1:11434"
    ollama_context_tokens: int = Field(default=8192, ge=2048, le=32768)
    ollama_max_output_tokens: int = Field(default=512, ge=32, le=2048)
    ollama_timeout_seconds: float = Field(default=180, ge=1, le=600)
    stream_heartbeat_seconds: float = Field(default=10, ge=0.005, le=60)
    openai_api_key: SecretStr | None = Field(default=None, validation_alias="OPENAI_API_KEY")
    api_key: SecretStr | None = None
    chroma_host: str | None = None
    chroma_port: int = 8000
    chunk_strategy: Literal["sentence", "semantic"] = "sentence"
    chunk_words: int = Field(default=180, ge=40, le=400)
    chunk_overlap_words: int = Field(default=24, ge=0)
    semantic_threshold: float = Field(default=0.35, ge=-1, le=1)
    max_upload_mb: int = Field(default=20, ge=1, le=100)
    max_pages: int = Field(default=200, ge=1)
    max_document_chars: int = Field(default=1_000_000, ge=1000)
    max_chunks: int = Field(default=2000, ge=1)
    max_corpus_chunks: int = Field(default=20_000, ge=1)
    context_chars: int = Field(default=18_000, ge=1000, le=100_000)
    history_turns: int = Field(default=6, ge=0, le=20)
    worker_enabled: bool = True
    auto_migrate: bool = True

    @model_validator(mode="after")
    def validate_configuration(self):
        if not self.llm_model:
            self.llm_model = "qwen3:1.7b" if self.llm_provider == "ollama" else "gpt-4.1-mini"
        if self.ollama_max_output_tokens + 1024 >= self.ollama_context_tokens:
            raise ValueError("Ollama context must leave room for input and output")
        if self.chunk_overlap_words >= self.chunk_words:
            raise ValueError("chunk_overlap_words must be smaller than chunk_words")
        if self.chunk_strategy == "semantic" and self.embedding_provider == "hash":
            raise ValueError("Semantic chunking requires fastembed or openai embeddings")
        if "openai" in (self.embedding_provider, self.llm_provider) and not self.openai_api_key:
            raise ValueError("OPENAI_API_KEY is required for the OpenAI provider")
        if not self.embedding_model:
            self.embedding_model = {
                "hash": "hash-lexical-v1-384",
                "fastembed": "BAAI/bge-small-en-v1.5",
                "openai": "text-embedding-3-small",
            }[self.embedding_provider]
        return self
