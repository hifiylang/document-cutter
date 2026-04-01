from __future__ import annotations
"""对外请求/响应模型与内部标准节点模型。"""

from typing import Any, Literal

from pydantic import BaseModel, Field


NodeType = Literal["title", "paragraph", "table", "list", "unknown"]


class DocumentNode(BaseModel):
    """统一的文档中间结构，所有解析器最终都要落到这里。"""
    node_id: str
    node_type: NodeType
    level: int = 0
    text: str
    source_page: int | None = None
    source_meta: dict[str, Any] = Field(default_factory=dict)


class Chunk(BaseModel):
    """最终返回给调用方的切分结果。"""
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
    """切分参数，既可由环境变量提供，也可由请求覆盖。"""
    target_chunk_chars: int = Field(default=1200, ge=200)
    min_chunk_chars: int = Field(default=400, ge=50)
    max_chunk_chars: int = Field(default=1800, ge=200)
    overlap_chars: int = Field(default=80, ge=0)
    similarity_enabled: bool = True
    llm_enabled: bool = False


class ChunkByUrlRequest(BaseModel):
    document_url: str
    filename: str
    options: ChunkOptions | None = None
