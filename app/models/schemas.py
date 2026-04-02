from __future__ import annotations
"""Public request/response schemas and the internal document node model."""

from typing import Any, Literal

from pydantic import BaseModel, Field


NodeType = Literal["title", "paragraph", "table", "list", "unknown"]


class DocumentNode(BaseModel):
    """Unified intermediate node produced by every parser."""

    node_id: str
    node_type: NodeType
    level: int = 0
    text: str
    source_page: int | None = None
    source_meta: dict[str, Any] = Field(default_factory=dict)


class Chunk(BaseModel):
    """Public chunk payload returned by the service."""

    chunk_id: str
    text: str
    char_count: int
    token_estimate: int
    source_node_ids: list[str]
    section_path: list[str] = Field(default_factory=list)
    metadata: dict[str, Any] = Field(default_factory=dict)


class ChunkResponse(BaseModel):
    document_id: str
    filename: str
    total_nodes: int
    total_chunks: int
    chunks: list[Chunk]


class HealthResponse(BaseModel):
    status: str = "ok"


class ChunkOptions(BaseModel):
    """Token-first chunking options used by the pipeline."""

    target_chunk_tokens: int = Field(default=300, ge=50)
    min_chunk_tokens: int = Field(default=100, ge=1)
    max_chunk_tokens: int = Field(default=450, ge=10)
    overlap_ratio: float = Field(default=0.0, ge=0.0, le=0.9)
    overlap_tokens: int = Field(default=0, ge=0)
    similarity_enabled: bool = True
    llm_enabled: bool = False


class ChunkByUrlRequest(BaseModel):
    document_url: str
    filename: str
    options: ChunkOptions | None = None
