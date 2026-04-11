from __future__ import annotations

"""对外请求响应模型，以及内部统一节点模型。"""

from typing import Any, Literal

from pydantic import BaseModel, Field


NodeType = Literal["title", "paragraph", "table", "list", "unknown"]
TaskStatus = Literal["queued", "processing", "completed", "failed"]


class DocumentNode(BaseModel):
    """所有解析器统一产出的中间节点。"""

    node_id: str
    node_type: NodeType
    level: int = 0
    text: str
    source_page: int | None = None
    source_meta: dict[str, Any] = Field(default_factory=dict)


class SectionBatch(BaseModel):
    """章节级处理中间结构。"""

    section_index: int
    section_path: list[str] = Field(default_factory=list)
    nodes: list[DocumentNode] = Field(default_factory=list)


class ChunkMetadata(BaseModel):
    """对外暴露的最小 chunk 元信息。"""

    chunk_type: str
    page_no: list[int] = Field(default_factory=list)


class Chunk(BaseModel):
    """内部切分结果结构。"""

    chunk_id: str
    text: str
    chunk_index: int | None = None
    section_path: list[str] = Field(default_factory=list)
    metadata: ChunkMetadata


class ChunkResponse(BaseModel):
    """内部切分接口的完整响应。"""

    document_id: str
    filename: str
    total_chunks: int
    chunks: list[Chunk]


StreamEventType = Literal[
    "document_started",
    "chunk_generated",
    "section_completed",
    "document_completed",
    "document_failed",
]


class ChunkStreamEvent(BaseModel):
    """SSE 返回的流式事件结构。"""

    event: StreamEventType
    document_id: str
    filename: str
    chunk: Chunk | None = None
    chunk_index: int | None = None
    section_index: int | None = None
    section_path: list[str] = Field(default_factory=list)
    total_chunks: int | None = None
    is_last_chunk_in_section: bool | None = None
    message: str | None = None


class StoredDocumentResponse(BaseModel):
    """文档入库后的摘要响应。"""

    document_id: str
    filename: str
    status: str
    total_chunks: int


class ChunkListItem(BaseModel):
    """分页列表中的 chunk 预览。"""

    chunk_id: str
    preview_text: str
    section_path: list[str] = Field(default_factory=list)
    metadata: ChunkMetadata


class ChunkListResponse(BaseModel):
    """文档 chunk 分页列表。"""

    document_id: str
    filename: str
    total_chunks: int
    page: int
    page_size: int
    items: list[ChunkListItem]


class ChunkDetailResponse(BaseModel):
    """单个 chunk 的详情响应。"""

    chunk_id: str
    document_id: str
    text: str
    section_path: list[str] = Field(default_factory=list)
    metadata: ChunkMetadata


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


class DocumentTask(BaseModel):
    """文档任务状态。"""

    task_id: str
    document_id: str
    filename: str
    status: TaskStatus
    progress_message: str | None = None
    error_message: str | None = None
    created_at: float
    updated_at: float


class TaskEvent(BaseModel):
    """任务级 SSE 事件。"""

    event: Literal["task_queued", "task_processing", "task_completed", "task_failed"]
    task_id: str
    document_id: str
    filename: str
    status: TaskStatus
    progress_message: str | None = None
    error_message: str | None = None
