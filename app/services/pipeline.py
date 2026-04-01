from __future__ import annotations
"""文档切分主编排入口。"""

from pathlib import Path

import httpx

from app.core.config import settings
from app.core.errors import DownloadError, FileTooLargeError, OcrRequiredError, UnsupportedFileTypeError
from app.models.schemas import ChunkOptions, ChunkResponse, DocumentNode
from app.services.boundary import BoundaryDecisionEngine
from app.services.merger import ChunkMerger
from app.services.normalizer import DocumentNormalizer
from app.services.parser import get_parser, is_image_filename
from app.services.segmenter import SemanticSegmenter
from app.services.serializer import ChunkSerializer
from app.services.splitter import ChunkSplitter
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
        # 解析、切分、增强都拆成独立组件，便于后续单独替换或调优。
        self.normalizer = DocumentNormalizer()
        self.segmenter = SemanticSegmenter()
        self.merger = ChunkMerger()
        self.splitter = ChunkSplitter()
        self.serializer = ChunkSerializer()
        self.boundary_engine = BoundaryDecisionEngine()
        self.visual_analyzer = VisualDocumentAnalyzer()

    def chunk_bytes(
        self,
        file_bytes: bytes,
        filename: str,
        options: ChunkOptions | None = None,
    ) -> ChunkResponse:
        # 统一在入口补齐默认参数，避免路由层和内部调用出现配置漂移。
        options = options or ChunkOptions(
            target_chunk_chars=settings.target_chunk_chars,
            min_chunk_chars=settings.min_chunk_chars,
            max_chunk_chars=settings.max_chunk_chars,
            overlap_chars=settings.overlap_chars,
            similarity_enabled=settings.similarity_enabled,
            llm_enabled=settings.llm_enabled,
        )
        self._validate_file(filename, file_bytes)
        nodes = self._extract_nodes(file_bytes, filename)
        nodes = self.normalizer.normalize(nodes)
        if not nodes:
            suffix = Path(filename).suffix.lower()
            if suffix == ".pdf" or is_image_filename(filename):
                raise OcrRequiredError("ocr required for scanned or image-only document")
            raise ValueError("document contains no extractable text")

        # 主链路顺序固定：先按结构切，再做长度治理，最后才做边界增强。
        blocks = self.segmenter.segment(nodes)
        blocks = self.merger.merge(blocks, options)
        blocks = self.splitter.split(blocks, options)
        blocks = self.boundary_engine.refine_blocks(blocks, options)
        return self.serializer.serialize(filename, blocks)

    def chunk_url(self, document_url: str, filename: str, options: ChunkOptions | None = None) -> ChunkResponse:
        try:
            content = self._download_url(document_url)
        except DownloadError:
            raise
        except Exception as exc:
            raise DownloadError(f"failed to download document from {document_url}") from exc
        return self.chunk_bytes(content, filename, options)

    def _extract_nodes(self, file_bytes: bytes, filename: str) -> list[DocumentNode]:
        if is_image_filename(filename):
            return self._analyze_image_document(file_bytes, filename)

        nodes = self._parse_document(file_bytes, filename)
        # PDF 如果文本极少，通常是扫描件或图片型内容，这时主动回退到视觉 OCR。
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
        total_chars = sum(len(node.text) for node in nodes)
        return total_chars < settings.pdf_ocr_fallback_min_chars

    def _download_url(self, document_url: str) -> bytes:
        try:
            with httpx.Client(timeout=settings.http_timeout_seconds, follow_redirects=True) as client:
                response = client.get(document_url)
                response.raise_for_status()
                return response.content
        except Exception as exc:  # pragma: no cover - exercised via API boundary
            raise DownloadError(f"failed to download document from {document_url}") from exc

    def _validate_file(self, filename: str, file_bytes: bytes) -> None:
        suffix = Path(filename).suffix.lower()
        if suffix not in SUPPORTED_SUFFIXES:
            raise UnsupportedFileTypeError(f"unsupported file type: {suffix or filename}")
        # 文件大小在入口就拦住，避免后续解析、OCR、模型调用被大文件拖垮。
        if len(file_bytes) > settings.max_upload_mb * 1024 * 1024:
            raise FileTooLargeError(f"file exceeds max size of {settings.max_upload_mb} MB")
