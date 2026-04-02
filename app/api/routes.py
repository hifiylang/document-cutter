from __future__ import annotations

"""文档切分服务的对外 HTTP 路由。"""

import asyncio

from fastapi import APIRouter, File, Form, UploadFile

from app.core.config import settings
from app.core.errors import ProcessingTimeoutError, to_http_error
from app.models.schemas import ChunkByUrlRequest, ChunkOptions, ChunkResponse, HealthResponse
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


@router.post("/v1/chunk/by-upload", response_model=ChunkResponse)
async def chunk_by_upload(
    file: UploadFile = File(...),
    target_chunk_tokens: int | None = Form(default=None),
    min_chunk_tokens: int | None = Form(default=None),
    max_chunk_tokens: int | None = Form(default=None),
    overlap_ratio: float | None = Form(default=None),
    overlap_tokens: int | None = Form(default=None),
    similarity_enabled: bool | None = Form(default=None),
    llm_enabled: bool | None = Form(default=None),
) -> ChunkResponse:
    """上传文件并返回切分结果。"""

    try:
        payload = await file.read()
        options = ChunkOptions(
            target_chunk_tokens=settings.target_chunk_tokens if target_chunk_tokens is None else target_chunk_tokens,
            min_chunk_tokens=settings.min_chunk_tokens if min_chunk_tokens is None else min_chunk_tokens,
            max_chunk_tokens=settings.max_chunk_tokens if max_chunk_tokens is None else max_chunk_tokens,
            overlap_ratio=settings.overlap_ratio if overlap_ratio is None else overlap_ratio,
            overlap_tokens=settings.overlap_tokens if overlap_tokens is None else overlap_tokens,
            similarity_enabled=settings.similarity_enabled if similarity_enabled is None else similarity_enabled,
            llm_enabled=settings.llm_enabled if llm_enabled is None else llm_enabled,
        )
        return await _run_with_timeout(
            pipeline.chunk_bytes,
            payload,
            file.filename or "uploaded.txt",
            options,
        )
    except Exception as exc:
        raise to_http_error(exc) from exc


@router.post("/v1/chunk/by-url", response_model=ChunkResponse)
async def chunk_by_url(request: ChunkByUrlRequest) -> ChunkResponse:
    """按远程 URL 拉取文档并返回切分结果。"""

    try:
        return await _run_with_timeout(
            pipeline.chunk_url,
            request.document_url,
            request.filename,
            request.options,
        )
    except Exception as exc:
        raise to_http_error(exc) from exc
