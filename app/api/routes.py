from __future__ import annotations
"""对外 API，负责参数接收、超时控制和错误转换。"""

import asyncio

from fastapi import APIRouter, File, Form, UploadFile

from app.core.config import settings
from app.core.errors import ProcessingTimeoutError, to_http_error
from app.models.schemas import ChunkByUrlRequest, ChunkOptions, ChunkResponse, HealthResponse
from app.services.pipeline import DocumentChunkPipeline


router = APIRouter()
pipeline = DocumentChunkPipeline()


async def _run_with_timeout(func, *args):
    try:
        # 解析链路包含 OCR / 模型调用，统一套线程 + 超时保护。
        return await asyncio.wait_for(
            asyncio.to_thread(func, *args),
            timeout=settings.request_timeout_seconds,
        )
    except TimeoutError as exc:
        raise ProcessingTimeoutError("document processing timed out") from exc


@router.get("/health", response_model=HealthResponse)
def health() -> HealthResponse:
    return HealthResponse()


@router.post("/v1/chunk/by-upload", response_model=ChunkResponse)
async def chunk_by_upload(
    file: UploadFile = File(...),
    target_chunk_chars: int | None = Form(default=None),
    min_chunk_chars: int | None = Form(default=None),
    max_chunk_chars: int | None = Form(default=None),
    overlap_chars: int | None = Form(default=None),
    similarity_enabled: bool | None = Form(default=None),
    llm_enabled: bool | None = Form(default=None),
) -> ChunkResponse:
    try:
        payload = await file.read()
        # 路由层只负责组装入参，不在这里做任何切分逻辑判断。
        options = ChunkOptions(
            target_chunk_chars=target_chunk_chars or 1200,
            min_chunk_chars=min_chunk_chars or 400,
            max_chunk_chars=max_chunk_chars or 1800,
            overlap_chars=overlap_chars or 80,
            similarity_enabled=True if similarity_enabled is None else similarity_enabled,
            llm_enabled=llm_enabled or False,
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
    try:
        return await _run_with_timeout(
            pipeline.chunk_url,
            request.document_url,
            request.filename,
            request.options,
        )
    except Exception as exc:
        raise to_http_error(exc) from exc
