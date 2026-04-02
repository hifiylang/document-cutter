from __future__ import annotations

"""解析器兼容入口。

为了让现有调用方和测试不需要改导入路径，这里继续保留 `app.services.parser`，
实际实现已经拆到 `app.services.parsers` 包下。
"""

from app.services.parsers import (
    BaseParser,
    DocxParser,
    PdfImageRegion,
    PdfParser,
    TxtMarkdownParser,
    XlsParser,
    XlsxParser,
    get_parser,
    is_image_filename,
)

__all__ = [
    "BaseParser",
    "PdfImageRegion",
    "TxtMarkdownParser",
    "DocxParser",
    "PdfParser",
    "XlsxParser",
    "XlsParser",
    "get_parser",
    "is_image_filename",
]
