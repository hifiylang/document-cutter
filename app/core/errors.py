from __future__ import annotations
"""统一异常定义与 HTTP 错误映射。"""

from fastapi import HTTPException, status


class DocumentCutterError(Exception):
    """Base error for document cutter failures."""


class UnsupportedFileTypeError(DocumentCutterError):
    pass


class DownloadError(DocumentCutterError):
    pass


class FileTooLargeError(DocumentCutterError):
    pass


class ProcessingTimeoutError(DocumentCutterError):
    pass


class OcrRequiredError(DocumentCutterError):
    pass


def to_http_error(exc: Exception) -> HTTPException:
    # 所有业务异常在这里收口，避免路由层四处散落状态码逻辑。
    if isinstance(exc, FileTooLargeError):
        return HTTPException(status_code=status.HTTP_413_REQUEST_ENTITY_TOO_LARGE, detail=str(exc))
    if isinstance(exc, ProcessingTimeoutError):
        return HTTPException(status_code=status.HTTP_504_GATEWAY_TIMEOUT, detail=str(exc))
    if isinstance(exc, OcrRequiredError):
        return HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail=str(exc))
    if isinstance(exc, (UnsupportedFileTypeError, ValueError)):
        return HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc))
    if isinstance(exc, DownloadError):
        return HTTPException(status_code=status.HTTP_502_BAD_GATEWAY, detail=str(exc))
    return HTTPException(status_code=status.HTTP_500_INTERNAL_SERVER_ERROR, detail="internal server error")
