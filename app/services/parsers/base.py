from __future__ import annotations

"""解析器公共抽象与共享常量。"""

from abc import ABC, abstractmethod
from dataclasses import dataclass
from pathlib import Path

from app.models.schemas import DocumentNode


IMAGE_SUFFIXES = {".png", ".jpg", ".jpeg", ".webp", ".bmp", ".tif", ".tiff"}


@dataclass
class PdfImageRegion:
    """PDF 页面内需要单独送视觉模型解析的图片区域。"""

    page_no: int
    bbox: list[float]
    image_region_id: str
    order: int
    parser_strategy: str = "pdf_image_region"
    layout_role: str = "image_region"


class BaseParser(ABC):
    """所有文档解析器统一遵循的接口。"""

    @abstractmethod
    def parse(self, file_bytes: bytes, filename: str) -> list[DocumentNode]:
        """把原始文件字节流转换成标准 DocumentNode。"""
        raise NotImplementedError


def is_image_filename(filename: str) -> bool:
    """判断文件名是否属于图片类型。"""

    return Path(filename).suffix.lower() in IMAGE_SUFFIXES
