from __future__ import annotations

"""PDF 解析器，负责正文、表格、图片区域与页内重排。"""

import io
import logging
import re
import uuid
from collections import Counter

from pypdf import PdfReader

from app.core.metrics import (
    PDF_IMAGE_REGION_DETECTED,
    PDF_IMAGE_REGION_VISION_ERROR,
    PDF_IMAGE_REGION_VISION_SUCCESS,
)
from app.models.schemas import DocumentNode
from app.services.parsers.base import BaseParser, PdfImageRegion
from app.services.vision import VisualDocumentAnalyzer

try:
    import fitz
except Exception:  # pragma: no cover
    fitz = None  # type: ignore


logger = logging.getLogger(__name__)


class PdfParser(BaseParser):
    """优先用版面解析提取 PDF，必要时补充图片区域视觉识别。"""

    page_number_re = re.compile(r"^(?:page\s*\d+|第\s*\d+\s*页|\d+\s*/\s*\d+)$", re.IGNORECASE)

    def __init__(self) -> None:
        self.visual_analyzer = VisualDocumentAnalyzer()

    def parse(self, file_bytes: bytes, filename: str) -> list[DocumentNode]:
        nodes: list[DocumentNode] = []
        if fitz is not None:
            nodes = self._extract_with_pymupdf(file_bytes, filename)
        if not nodes:
            nodes = self._extract_with_pypdf(file_bytes)
        return self._remove_repeated_page_noise(nodes)

    def _extract_with_pymupdf(self, file_bytes: bytes, filename: str) -> list[DocumentNode]:
        document = fitz.open(stream=file_bytes, filetype="pdf")
        nodes: list[DocumentNode] = []
        for page_index, page in enumerate(document, start=1):
            page_height = float(page.rect.height or 1)
            text_blocks = page.get_text("blocks")
            page_tables = self._extract_tables_from_page(page, page_index)
            table_bboxes = [table.source_meta.get("bbox") for table in page_tables if table.source_meta.get("bbox")]
            page_regions = self._extract_image_regions(page, page_index, table_bboxes)
            page_text_nodes = self._extract_text_nodes(page_index, page_height, text_blocks, table_bboxes)
            page_image_nodes = self._extract_image_nodes(page, page_index, filename, page_regions)
            page_nodes = self._assemble_page_nodes(page_text_nodes, page_tables, page_image_nodes)
            logger.info(
                "pdf page=%s image_regions=%s image_nodes=%s text_nodes=%s table_nodes=%s",
                page_index,
                len(page_regions),
                len(page_image_nodes),
                len(page_text_nodes),
                len(page_tables),
            )
            nodes.extend(page_nodes)
        return nodes

    def _extract_text_nodes(
        self,
        page_index: int,
        page_height: float,
        blocks,
        table_bboxes: list[object],
    ) -> list[DocumentNode]:
        nodes: list[DocumentNode] = []
        for block in self._sort_blocks_by_columns(blocks):
            x0, y0, x1, y1, text, *_ = block
            bbox = [float(x0), float(y0), float(x1), float(y1)]
            if self._overlaps_any(bbox, table_bboxes):
                continue
            cleaned = self._clean_pdf_text(text)
            if not cleaned:
                continue
            node_type, level = self._infer_pdf_node_type(cleaned)
            nodes.append(
                DocumentNode(
                    node_id=str(uuid.uuid4()),
                    node_type=node_type,
                    level=level,
                    text=cleaned,
                    source_page=page_index,
                    source_meta={
                        "bbox": bbox,
                        "layout_role": self._layout_role(float(y0), float(y1), page_height),
                        "parser_strategy": "pymupdf",
                        "modality": "text",
                        "page_layout": f"page_{page_index}",
                    },
                )
            )
        return nodes

    def _extract_image_regions(self, page, page_index: int, table_bboxes: list[object]) -> list[PdfImageRegion]:
        page_dict = page.get_text("dict")
        regions: list[PdfImageRegion] = []
        order = 0
        page_area = max(float(page.rect.width * page.rect.height), 1.0)
        for block in page_dict.get("blocks", []):
            if block.get("type") != 1:
                continue
            bbox = block.get("bbox")
            if not isinstance(bbox, (list, tuple)) or len(bbox) != 4:
                continue
            normalized_bbox = [float(value) for value in bbox]
            area = max((normalized_bbox[2] - normalized_bbox[0]) * (normalized_bbox[3] - normalized_bbox[1]), 0.0)
            if area / page_area < 0.01:
                continue
            if self._overlaps_any(normalized_bbox, table_bboxes):
                continue
            regions.append(
                PdfImageRegion(
                    page_no=page_index,
                    bbox=normalized_bbox,
                    image_region_id=str(uuid.uuid4()),
                    order=order,
                )
            )
            order += 1

        if not regions:
            for index, block in enumerate(page.get_text("blocks")):
                x0, y0, x1, y1, text, *_ = block
                normalized_bbox = [float(x0), float(y0), float(x1), float(y1)]
                area = max((normalized_bbox[2] - normalized_bbox[0]) * (normalized_bbox[3] - normalized_bbox[1]), 0.0)
                block_text = (text or "").strip()
                if block_text and not block_text.lower().startswith("<image:"):
                    continue
                if area / page_area < 0.03:
                    continue
                if self._overlaps_any(normalized_bbox, table_bboxes):
                    continue
                regions.append(
                    PdfImageRegion(
                        page_no=page_index,
                        bbox=normalized_bbox,
                        image_region_id=str(uuid.uuid4()),
                        order=index,
                        parser_strategy="pdf_image_region_heuristic",
                    )
                )

        for _ in regions:
            PDF_IMAGE_REGION_DETECTED.inc()
        return regions

    def _extract_image_nodes(
        self,
        page,
        page_index: int,
        filename: str,
        image_regions: list[PdfImageRegion],
    ) -> list[DocumentNode]:
        image_nodes: list[DocumentNode] = []
        for region in image_regions:
            try:
                pixmap = page.get_pixmap(matrix=fitz.Matrix(2, 2), clip=fitz.Rect(*region.bbox), alpha=False)
                region_nodes = self.visual_analyzer.analyze_cropped_region(
                    pixmap.tobytes("png"),
                    f"{filename}-page-{page_index}-region-{region.order}.png",
                    page_no=page_index,
                    bbox=region.bbox,
                    image_region_id=region.image_region_id,
                )
                for node in region_nodes:
                    node.source_meta.setdefault("bbox", region.bbox)
                    node.source_meta.setdefault("layout_role", region.layout_role)
                    node.source_meta.setdefault("image_region_id", region.image_region_id)
                    node.source_meta.setdefault("parser_strategy", "vision_image_region")
                    node.source_meta.setdefault("modality", "image")
                    node.source_meta.setdefault("order", region.order)
                    node.source_meta.setdefault("page_layout", f"page_{page_index}")
                image_nodes.extend(region_nodes)
                PDF_IMAGE_REGION_VISION_SUCCESS.inc()
            except Exception:
                PDF_IMAGE_REGION_VISION_ERROR.inc()
        return image_nodes

    def _assemble_page_nodes(
        self,
        text_nodes: list[DocumentNode],
        table_nodes: list[DocumentNode],
        image_nodes: list[DocumentNode],
    ) -> list[DocumentNode]:
        def sort_key(node: DocumentNode) -> tuple[float, float, int]:
            bbox = node.source_meta.get("bbox")
            if isinstance(bbox, list) and len(bbox) == 4:
                return (float(bbox[1]), float(bbox[0]), int(node.source_meta.get("order") or 0))
            return (10**9, 10**9, int(node.source_meta.get("order") or 0))

        return sorted([*text_nodes, *table_nodes, *image_nodes], key=sort_key)

    def _extract_with_pypdf(self, file_bytes: bytes) -> list[DocumentNode]:
        reader = PdfReader(io.BytesIO(file_bytes))
        nodes: list[DocumentNode] = []
        for page_index, page in enumerate(reader.pages, start=1):
            raw = (page.extract_text() or "").strip()
            if not raw:
                continue
            sections = [section.strip() for section in re.split(r"\n{2,}", raw) if section.strip()]
            for section in sections:
                node_type, level = self._infer_pdf_node_type(section)
                nodes.append(
                    DocumentNode(
                        node_id=str(uuid.uuid4()),
                        node_type=node_type,
                        level=level,
                        text=section,
                        source_page=page_index,
                        source_meta={
                            "parser_strategy": "pypdf",
                            "modality": "text",
                            "page_layout": f"page_{page_index}",
                        },
                    )
                )
        return nodes

    def _extract_tables_from_page(self, page, page_index: int) -> list[DocumentNode]:
        if not hasattr(page, "find_tables"):
            return []
        try:
            tables = page.find_tables()
        except Exception:
            return []
        nodes: list[DocumentNode] = []
        for table in getattr(tables, "tables", []):
            rows = []
            for row in table.extract() or []:
                cells = ["" if cell is None else str(cell).strip() for cell in row]
                if any(cells):
                    rows.append(" | ".join(cells))
            table_text = "\n".join(rows).strip()
            if table_text:
                bbox = getattr(table, "bbox", None)
                nodes.append(
                    DocumentNode(
                        node_id=str(uuid.uuid4()),
                        node_type="table",
                        text=table_text,
                        source_page=page_index,
                        source_meta={
                            "layout_role": "table",
                            "parser_strategy": "pymupdf_table",
                            "bbox": list(bbox) if bbox else None,
                            "modality": "text",
                            "page_layout": f"page_{page_index}",
                        },
                    )
                )
        return nodes

    def _clean_pdf_text(self, text: str) -> str:
        lines = [re.sub(r"\s+", " ", line).strip() for line in text.splitlines()]
        lines = [line for line in lines if line]
        return "\n".join(lines).strip()

    def _infer_pdf_node_type(self, text: str) -> tuple[str, int]:
        stripped = text.strip()
        if "|" in stripped and stripped.count("|") >= 2:
            return "table", 0
        if re.match(r"^\s*(?:[-*+]|\d+[.)])\s+", stripped):
            return "list", 0
        first_line = stripped.splitlines()[0].strip()
        if len(stripped.splitlines()) == 1 and len(first_line) <= 60 and not re.search(r"[。?!;:：；]", first_line):
            return "title", 1
        return "paragraph", 0

    def _layout_role(self, y0: float, y1: float, page_height: float) -> str:
        if y1 <= page_height * 0.12:
            return "header_zone"
        if y0 >= page_height * 0.88:
            return "footer_zone"
        return "body"

    def _sort_blocks_by_columns(self, blocks) -> list[tuple]:
        centers = [((float(item[0]) + float(item[2])) / 2.0) for item in blocks if (item[4] or "").strip()]
        if not centers:
            return []
        median_x = sorted(centers)[len(centers) // 2]

        def key(item):
            x0, y0, x1, _y1, text, *_ = item
            if not (text or "").strip():
                return (99, 99, 99)
            center = (float(x0) + float(x1)) / 2.0
            column = 0 if center <= median_x else 1
            return (column, round(float(y0) / 12), float(x0))

        return sorted(blocks, key=key)

    def _overlaps_any(self, bbox: list[float], other_bboxes: list[object]) -> bool:
        x0, y0, x1, y1 = bbox
        for other in other_bboxes:
            if not isinstance(other, list) or len(other) != 4:
                continue
            ox0, oy0, ox1, oy1 = other
            if x0 < ox1 and x1 > ox0 and y0 < oy1 and y1 > oy0:
                return True
        return False

    def _remove_repeated_page_noise(self, nodes: list[DocumentNode]) -> list[DocumentNode]:
        text_counter: Counter[str] = Counter()
        for node in nodes:
            text = node.text.strip()
            if text and len(text) <= 100:
                text_counter[text] += 1

        repeated_texts = {text for text, count in text_counter.items() if count >= 2}
        cleaned: list[DocumentNode] = []
        for node in nodes:
            text = node.text.strip()
            layout_role = node.source_meta.get("layout_role")
            is_page_number = bool(self.page_number_re.match(text))
            is_repeated_margin = text in repeated_texts and layout_role in {None, "header_zone", "footer_zone"}
            if is_page_number or is_repeated_margin:
                continue
            cleaned.append(node)
        return cleaned
