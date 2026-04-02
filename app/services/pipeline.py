from __future__ import annotations

"""文档切分主流水线编排。"""

import ipaddress
import socket
from pathlib import Path
from urllib.parse import urlsplit

import httpx

from app.core.config import settings
from app.core.errors import DownloadError, FileTooLargeError, OcrRequiredError, UnsupportedFileTypeError
from app.models.schemas import ChunkOptions, ChunkResponse, DocumentNode
from app.services.normalizer import DocumentNormalizer
from app.services.parser import get_parser, is_image_filename
from app.services.selection import RuntimeSelector
from app.services.serializer import ChunkSerializer
from app.services.text_chunker import TextChunker
from app.services.token_counter import TokenCounter
from app.services.vision import VisualDocumentAnalyzer


SUPPORTED_SUFFIXES = {
    ".pdf",
    ".docx",
    ".txt",
    ".md",
    ".xlsx",
    ".xls",
    ".png",
    ".jpg",
    ".jpeg",
    ".webp",
    ".bmp",
    ".tif",
    ".tiff",
}

ALLOWED_CONTENT_TYPES = {
    ".pdf": {"application/pdf", "application/octet-stream"},
    ".docx": {
        "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
        "application/octet-stream",
    },
    ".txt": {"text/plain", "application/octet-stream"},
    ".md": {"text/markdown", "text/plain", "application/octet-stream"},
    ".xlsx": {
        "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        "application/octet-stream",
    },
    ".xls": {"application/vnd.ms-excel", "application/octet-stream"},
}

IMAGE_SUFFIXES = {".png", ".jpg", ".jpeg", ".webp", ".bmp", ".tif", ".tiff"}


class DocumentChunkPipeline:
    """组织解析、标准化、切分和序列化的总入口。"""

    def __init__(self) -> None:
        self.token_counter = TokenCounter()
        self.normalizer = DocumentNormalizer()
        self.chunker = TextChunker(self.token_counter)
        self.serializer = ChunkSerializer(self.token_counter)
        self.visual_analyzer = VisualDocumentAnalyzer()
        self.selector = RuntimeSelector()

    def chunk_bytes(
        self,
        file_bytes: bytes,
        filename: str,
        options: ChunkOptions | None = None,
    ) -> ChunkResponse:
        """从原始文件字节流直接完成解析、切分和序列化。"""

        options = options or ChunkOptions(
            target_chunk_tokens=settings.target_chunk_tokens,
            min_chunk_tokens=settings.min_chunk_tokens,
            max_chunk_tokens=settings.max_chunk_tokens,
            overlap_ratio=settings.overlap_ratio,
            overlap_tokens=settings.overlap_tokens,
        )
        self._validate_file(filename, file_bytes)
        nodes = self._extract_nodes(file_bytes, filename, options)
        nodes = self.normalizer.normalize(nodes)
        if not nodes:
            suffix = Path(filename).suffix.lower()
            if suffix == ".pdf" or is_image_filename(filename):
                raise OcrRequiredError("ocr required for scanned or image-only document")
            raise ValueError("document contains no extractable text")

        blocks = self.chunker.chunk(nodes, options)
        return self.serializer.serialize(
            filename,
            blocks,
            response_metadata=self.selector.to_response_metadata(options),
        )

    def chunk_url(self, document_url: str, filename: str, options: ChunkOptions | None = None) -> ChunkResponse:
        """先下载远程文档，再复用本地切分主链路。"""

        try:
            content = self._download_url(document_url, filename)
        except DownloadError:
            raise
        except Exception as exc:
            raise DownloadError(f"failed to download document from {document_url}") from exc
        return self.chunk_bytes(content, filename, options)

    def _extract_nodes(self, file_bytes: bytes, filename: str, options: ChunkOptions) -> list[DocumentNode]:
        """按文件类型选择最合适的解析方式。"""

        if is_image_filename(filename):
            return self._analyze_image_document(file_bytes, filename, options)

        nodes = self._parse_document(file_bytes, filename)
        if Path(filename).suffix.lower() == ".pdf" and self._should_fallback_to_pdf_ocr(nodes):
            vision_nodes = self._analyze_pdf_with_vision(file_bytes, filename, options)
            if vision_nodes:
                return vision_nodes
        return nodes

    def _parse_document(self, file_bytes: bytes, filename: str) -> list[DocumentNode]:
        parser = get_parser(filename)
        return parser.parse(file_bytes, filename)

    def _analyze_image_document(self, file_bytes: bytes, filename: str, options: ChunkOptions) -> list[DocumentNode]:
        return self.visual_analyzer.analyze_image_bytes(file_bytes, filename, options=options)

    def _analyze_pdf_with_vision(self, file_bytes: bytes, filename: str, options: ChunkOptions) -> list[DocumentNode]:
        return self.visual_analyzer.analyze_pdf_bytes(file_bytes, filename, options=options)

    def _should_fallback_to_pdf_ocr(self, nodes: list[DocumentNode]) -> bool:
        """PDF 提取出的正文过少时，进入整页 OCR 回退链路。"""

        total_chars = sum(len(node.text) for node in nodes)
        return total_chars < settings.pdf_ocr_fallback_min_chars

    def _download_url(self, document_url: str, filename: str) -> bytes:
        parsed = urlsplit(document_url)
        if parsed.scheme not in {"http", "https"}:
            raise DownloadError("only http and https URLs are allowed")
        if not parsed.hostname:
            raise DownloadError("document URL must include a hostname")

        self._validate_allowed_host(parsed.hostname)
        self._validate_remote_host(parsed.hostname)

        max_bytes = settings.max_upload_mb * 1024 * 1024 * settings.download_size_guard_factor
        suffix = Path(filename).suffix.lower()

        try:
            with httpx.Client(timeout=settings.http_timeout_seconds, follow_redirects=False) as client:
                with client.stream("GET", document_url) as response:
                    response.raise_for_status()
                    self._validate_content_type(suffix, response.headers.get("Content-Type"))

                    content_length = response.headers.get("Content-Length")
                    if content_length and content_length.isdigit() and int(content_length) > max_bytes:
                        raise FileTooLargeError(
                            f"remote file exceeds guarded download limit of {max_bytes // (1024 * 1024)} MB"
                        )

                    content = bytearray()
                    for chunk in response.iter_bytes():
                        if not chunk:
                            continue
                        content.extend(chunk)
                        if len(content) > max_bytes:
                            raise FileTooLargeError(
                                f"remote file exceeds guarded download limit of {max_bytes // (1024 * 1024)} MB"
                            )
                    return bytes(content)
        except (DownloadError, FileTooLargeError):
            raise
        except Exception as exc:
            raise DownloadError(f"failed to download document from {document_url}") from exc

    def _validate_allowed_host(self, hostname: str) -> None:
        """如果配置了白名单，则只允许访问白名单域名。"""

        raw = settings.download_allowed_hosts
        if not raw:
            return

        allowed_hosts = [item.strip().lower() for item in raw.split(",") if item.strip()]
        hostname = hostname.lower()
        for allowed in allowed_hosts:
            if hostname == allowed or hostname.endswith(f".{allowed}"):
                return
        raise DownloadError("document URL host is not in the allowed host list")

    def _validate_remote_host(self, hostname: str) -> None:
        """阻止访问内网、回环等高风险地址。"""

        try:
            addresses = {
                info[4][0]
                for info in socket.getaddrinfo(hostname, None, proto=socket.IPPROTO_TCP)
            }
        except socket.gaierror as exc:
            raise DownloadError("failed to resolve document URL host") from exc

        for address in addresses:
            ip = ipaddress.ip_address(address)
            if (
                ip.is_private
                or ip.is_loopback
                or ip.is_link_local
                or ip.is_multicast
                or ip.is_reserved
                or ip.is_unspecified
            ):
                raise DownloadError("document URL resolves to a disallowed private or special-use address")

    def _validate_content_type(self, suffix: str, content_type: str | None) -> None:
        """按文件后缀校验远程响应的 Content-Type。"""

        if not content_type:
            raise DownloadError("remote document response is missing Content-Type")

        normalized = content_type.split(";", 1)[0].strip().lower()
        if suffix in IMAGE_SUFFIXES:
            if normalized.startswith("image/") or normalized == "application/octet-stream":
                return
            raise DownloadError(f"unexpected Content-Type for image document: {normalized}")

        allowed = ALLOWED_CONTENT_TYPES.get(suffix)
        if allowed and normalized in allowed:
            return
        if allowed:
            raise DownloadError(f"unexpected Content-Type for {suffix or 'document'}: {normalized}")

    def _validate_file(self, filename: str, file_bytes: bytes) -> None:
        suffix = Path(filename).suffix.lower()
        if suffix not in SUPPORTED_SUFFIXES:
            raise UnsupportedFileTypeError(f"unsupported file type: {suffix or filename}")
        if len(file_bytes) > settings.max_upload_mb * 1024 * 1024:
            raise FileTooLargeError(f"file exceeds max size of {settings.max_upload_mb} MB")
