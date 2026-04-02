from __future__ import annotations

"""文档解析器导出与工厂方法。"""

from app.services.parsers.base import BaseParser, PdfImageRegion, is_image_filename
from app.services.parsers.office import DocParser, DocxParser, XlsParser, XlsxParser
from app.services.parsers.pdf import PdfParser
from app.services.parsers.text import TxtMarkdownParser


def get_parser(filename: str) -> BaseParser:
    """按文件后缀返回对应解析器。"""

    lower = filename.lower()
    if lower.endswith(".doc"):
        return DocParser()
    if lower.endswith(".docx"):
        return DocxParser()
    if lower.endswith(".pdf"):
        return PdfParser()
    if lower.endswith(".xlsx"):
        return XlsxParser()
    if lower.endswith(".xls"):
        return XlsParser()
    if lower.endswith(".txt") or lower.endswith(".md"):
        return TxtMarkdownParser()
    raise ValueError(f"Unsupported file type for {filename}")


__all__ = [
    "BaseParser",
    "PdfImageRegion",
    "TxtMarkdownParser",
    "DocParser",
    "DocxParser",
    "PdfParser",
    "XlsxParser",
    "XlsParser",
    "get_parser",
    "is_image_filename",
]
