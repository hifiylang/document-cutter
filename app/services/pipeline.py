from __future__ import annotations
"""文档切分主流水线编排。"""

from pathlib import Path

import httpx

from app.core.config import settings
from app.core.errors import DownloadError, FileTooLargeError, OcrRequiredError, UnsupportedFileTypeError
from app.models.schemas import ChunkOptions, ChunkResponse, DocumentNode
from app.services.normalizer import DocumentNormalizer
from app.services.parser import get_parser, is_image_filename
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


class DocumentChunkPipeline:
    def __init__(self) -> None:
        self.token_counter = TokenCounter()
        self.normalizer = DocumentNormalizer()
        self.chunker = TextChunker(self.token_counter)
        self.serializer = ChunkSerializer(self.token_counter)
        self.visual_analyzer = VisualDocumentAnalyzer()

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
            similarity_enabled=settings.similarity_enabled,
            llm_enabled=settings.llm_enabled,
            embedding_base_url=settings.embedding_base_url,
            embedding_model=settings.embedding_model,
            embedding_api_key=settings.embedding_api_key,
        )
        self._validate_file(filename, file_bytes)
        nodes = self._extract_nodes(file_bytes, filename)
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
            response_metadata=self._build_response_metadata(options),
        )

    def chunk_url(self, document_url: str, filename: str, options: ChunkOptions | None = None) -> ChunkResponse:
        """先下载远程文档，再复用本地切分主链路。"""
        try:
            content = self._download_url(document_url)
        except DownloadError:
            raise
        except Exception as exc:
            raise DownloadError(f"failed to download document from {document_url}") from exc
        return self.chunk_bytes(content, filename, options)

    def _extract_nodes(self, file_bytes: bytes, filename: str) -> list[DocumentNode]:
        """按文件类型选择最合适的解析方式。"""
        if is_image_filename(filename):
            return self._analyze_image_document(file_bytes, filename)

        nodes = self._parse_document(file_bytes, filename)
        if Path(filename).suffix.lower() == ".pdf" and self._should_fallback_to_pdf_ocr(nodes):
            vision_nodes = self._analyze_pdf_with_vision(file_bytes, filename)
            if vision_nodes:
                return vision_nodes
        return nodes

    def _parse_document(self, file_bytes: bytes, filename: str) -> list[DocumentNode]:
        parser = get_parser(filename)
        return parser.parse(file_bytes, filename)

    def _analyze_image_document(self, file_bytes: bytes, filename: str) -> list[DocumentNode]:
        return self.visual_analyzer.analyze_image_bytes(file_bytes, filename)

    def _analyze_pdf_with_vision(self, file_bytes: bytes, filename: str) -> list[DocumentNode]:
        return self.visual_analyzer.analyze_pdf_bytes(file_bytes, filename)

    def _should_fallback_to_pdf_ocr(self, nodes: list[DocumentNode]) -> bool:
        """PDF 提取出的正文过少时，进入整页 OCR 回退链路。"""
        total_chars = sum(len(node.text) for node in nodes)
        return total_chars < settings.pdf_ocr_fallback_min_chars

    def _download_url(self, document_url: str) -> bytes:
        try:
            with httpx.Client(timeout=settings.http_timeout_seconds, follow_redirects=True) as client:
                response = client.get(document_url)
                response.raise_for_status()
                return response.content
        except Exception as exc:
            raise DownloadError(f"failed to download document from {document_url}") from exc

    def _validate_file(self, filename: str, file_bytes: bytes) -> None:
        suffix = Path(filename).suffix.lower()
        if suffix not in SUPPORTED_SUFFIXES:
            raise UnsupportedFileTypeError(f"unsupported file type: {suffix or filename}")
        if len(file_bytes) > settings.max_upload_mb * 1024 * 1024:
            raise FileTooLargeError(f"file exceeds max size of {settings.max_upload_mb} MB")

    def _build_response_metadata(self, options: ChunkOptions) -> dict[str, object]:
        """返回本次请求实际生效的关键模型与服务选择。"""
        selected_embedding_base_url = options.embedding_base_url or settings.embedding_base_url
        selected_embedding_model = options.embedding_model or settings.embedding_model
        selected_flash_model = settings.flash_model or settings.text_model
        return {
            "selected_options": {
                "embedding_base_url": selected_embedding_base_url,
                "embedding_model": selected_embedding_model,
                "flash_model": selected_flash_model,
                "vision_model": settings.vision_model,
            }
        }
