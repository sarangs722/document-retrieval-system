from datetime import datetime
from typing import Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, field_validator


class DocumentOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: str
    filename: str
    size_bytes: int
    status: str
    error: str | None
    page_count: int
    chunk_count: int
    index_signature: str | None
    created_at: datetime
    updated_at: datetime


class SearchRequest(BaseModel):
    query: str = Field(min_length=1, max_length=4000)
    top_k: int = Field(default=5, ge=1, le=12)
    document_ids: list[UUID] = Field(default_factory=list, max_length=100)
    mode: Literal["hybrid", "vector", "keyword"] = "hybrid"

    @field_validator("query")
    @classmethod
    def nonempty_query(cls, value):
        if not value.strip():
            raise ValueError("Query cannot be blank")
        return value.strip()


class SearchHit(BaseModel):
    chunk_id: str
    document_id: str
    filename: str
    page: int
    text: str
    score: float
    vector_score: float | None = None
    keyword_score: float | None = None


class Citation(SearchHit):
    label: int


class ChatRequest(SearchRequest):
    conversation_id: UUID | None = None


class ChatResponse(BaseModel):
    conversation_id: str
    answer: str
    citations: list[Citation]
    grounded: bool
    citation_status: Literal["valid", "missing", "invalid", "not_applicable"]
    provider: str
    retrieval_query: str
    latency_ms: float
