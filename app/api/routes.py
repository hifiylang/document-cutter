from __future__ import annotations

"""文档切分服务的对外 HTTP 路由。"""

import asyncio

from fastapi import APIRouter, File, Form, Query, UploadFile

from app.core.config import settings
from app.core.errors import NotFoundError, ProcessingTimeoutError, to_http_error
from app.models.schemas import (
    ChunkByUrlRequest,
    ChunkDetailResponse,
    ChunkListResponse,
    ChunkOptions,
    HealthResponse,
    StoredDocumentResponse,
)
from app.services.document_store import store
from app.services.pipeline import DocumentChunkPipeline


router = APIRouter()
pipeline = DocumentChunkPipeline()


async def _run_with_timeout(func, *args):
    """把同步切分逻辑放到线程池里执行，并统一加总超时。"""

    try:
        return await asyncio.wait_for(
            asyncio.to_thread(func, *args),
            timeout=settings.request_timeout_seconds,
        )
    except TimeoutError as exc:
        raise ProcessingTimeoutError("document processing timed out") from exc


@router.get("/health", response_model=HealthResponse)
def health() -> HealthResponse:
    """健康检查接口。"""

    return HealthResponse()


@router.post("/v1/chunk/by-upload", response_model=StoredDocumentResponse)
async def chunk_by_upload(
    file: UploadFile = File(...),
    target_chunk_tokens: int | None = Form(default=None),
    min_chunk_tokens: int | None = Form(default=None),
    max_chunk_tokens: int | None = Form(default=None),
    overlap_ratio: float | None = Form(default=None),
    overlap_tokens: int | None = Form(default=None),
) -> StoredDocumentResponse:
    """上传文件、完成切分并落库，返回文档摘要。"""

    try:
        payload = await file.read()
        options = ChunkOptions(
            target_chunk_tokens=settings.target_chunk_tokens if target_chunk_tokens is None else target_chunk_tokens,
            min_chunk_tokens=settings.min_chunk_tokens if min_chunk_tokens is None else min_chunk_tokens,
            max_chunk_tokens=settings.max_chunk_tokens if max_chunk_tokens is None else max_chunk_tokens,
            overlap_ratio=settings.overlap_ratio if overlap_ratio is None else overlap_ratio,
            overlap_tokens=settings.overlap_tokens if overlap_tokens is None else overlap_tokens,
        )
        result = await _run_with_timeout(
            pipeline.chunk_bytes,
            payload,
            file.filename or "uploaded.txt",
            options,
        )
        return StoredDocumentResponse.model_validate(store.save(result))
    except Exception as exc:
        raise to_http_error(exc) from exc


@router.post("/v1/chunk/by-url", response_model=StoredDocumentResponse)
async def chunk_by_url(request: ChunkByUrlRequest) -> StoredDocumentResponse:
    """按远程 URL 拉取文档、切分并落库，返回文档摘要。"""

    try:
        result = await _run_with_timeout(
            pipeline.chunk_url,
            request.document_url,
            request.filename,
            request.options,
        )
        return StoredDocumentResponse.model_validate(store.save(result))
    except Exception as exc:
        raise to_http_error(exc) from exc


@router.get("/v1/documents/{document_id}", response_model=StoredDocumentResponse)
def get_document(document_id: str) -> StoredDocumentResponse:
    """查询文档摘要。"""

    try:
        document = store.get_document(document_id)
        if not document:
            raise NotFoundError("document not found")
        return StoredDocumentResponse.model_validate(document)
    except Exception as exc:
        raise to_http_error(exc) from exc


@router.get("/v1/documents/{document_id}/chunks", response_model=ChunkListResponse)
def list_document_chunks(
    document_id: str,
    page: int = Query(default=1, ge=1),
    page_size: int = Query(default=20, ge=1, le=100),
) -> ChunkListResponse:
    """分页查询文档 chunk 列表。"""

    try:
        result = store.list_chunks(document_id, page, page_size)
        if not result:
            raise NotFoundError("document not found")
        return ChunkListResponse.model_validate(result)
    except Exception as exc:
        raise to_http_error(exc) from exc


@router.get("/v1/chunks/{chunk_id}", response_model=ChunkDetailResponse)
def get_chunk_detail(chunk_id: str) -> ChunkDetailResponse:
    """查询单个 chunk 的完整内容。"""

    try:
        chunk = store.get_chunk(chunk_id)
        if not chunk:
            raise NotFoundError("chunk not found")
        return ChunkDetailResponse.model_validate(chunk)
    except Exception as exc:
        raise to_http_error(exc) from exc
