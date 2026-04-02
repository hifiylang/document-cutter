from __future__ import annotations

"""对外请求响应模型，以及内部统一节点模型。"""

from typing import Any, Literal

from pydantic import BaseModel, Field


NodeType = Literal["title", "paragraph", "table", "list", "unknown"]


class DocumentNode(BaseModel):
    """所有解析器统一产出的中间节点。"""

    node_id: str
    node_type: NodeType
    level: int = 0
    text: str
    source_page: int | None = None
    source_meta: dict[str, Any] = Field(default_factory=dict)


class Chunk(BaseModel):
    """服务对外返回的标准切分结果。"""

    chunk_id: str
    text: str
    char_count: int
    token_estimate: int
    source_node_ids: list[str]
    section_path: list[str] = Field(default_factory=list)
    metadata: dict[str, Any] = Field(default_factory=dict)


class ChunkResponse(BaseModel):
    """文档切分接口的完整响应。"""

    document_id: str
    filename: str
    total_nodes: int
    total_chunks: int
    chunks: list[Chunk]
    metadata: dict[str, Any] = Field(default_factory=dict)


class HealthResponse(BaseModel):
    """健康检查响应。"""

    status: str = "ok"


class ChunkOptions(BaseModel):
    """主链路使用的 token-first 切分参数。"""

    target_chunk_tokens: int = Field(default=300, ge=50)
    min_chunk_tokens: int = Field(default=100, ge=1)
    max_chunk_tokens: int = Field(default=450, ge=10)
    overlap_ratio: float = Field(default=0.0, ge=0.0, le=0.9)
    overlap_tokens: int = Field(default=0, ge=0)


class ChunkByUrlRequest(BaseModel):
    """按 URL 拉取文档时的请求体。"""

    document_url: str
    filename: str
    options: ChunkOptions | None = None
