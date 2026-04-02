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
    """优先使用版面解析提取 PDF，必要时补充图片区域视觉识别。"""

    page_number_re = re.compile(r"^(?:page\s*\d+|第\s*\d+\s*页|\d+\s*/\s*\d+)$", re.IGNORECASE)
    numbered_heading_re = re.compile(
        r"^(?:第[一二三四五六七八九十百千万\d]+[章节部分篇]|[一二三四五六七八九十]+[、.]|\d+(?:\.\d+){0,3}[、.]?)\s*\S+"
    )
    list_re = re.compile(r"^\s*(?:[-*+•●]|\d+[.)]|（\d+）)\s+")

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
            page_text_nodes = self._merge_adjacent_text_nodes(page_text_nodes)
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

    def _merge_adjacent_text_nodes(self, nodes: list[DocumentNode]) -> list[DocumentNode]:
        """把同一列、同一段落的相邻文本块合并回自然段。"""

        if not nodes:
            return []

        merged: list[DocumentNode] = []
        current = nodes[0].model_copy(deep=True)
        for next_node in nodes[1:]:
            if self._should_merge_text_nodes(current, next_node):
                current.text = self._join_text(current.text, next_node.text)
                current.source_meta["bbox"] = self._union_bbox(
                    current.source_meta.get("bbox"),
                    next_node.source_meta.get("bbox"),
                )
                continue
            merged.append(current)
            current = next_node.model_copy(deep=True)
        merged.append(current)
        return merged

    def _should_merge_text_nodes(self, current: DocumentNode, next_node: DocumentNode) -> bool:
        if current.node_type != "paragraph" or next_node.node_type != "paragraph":
            return False
        if current.source_page != next_node.source_page:
            return False
        if current.source_meta.get("layout_role") != "body" or next_node.source_meta.get("layout_role") != "body":
            return False

        current_bbox = current.source_meta.get("bbox")
        next_bbox = next_node.source_meta.get("bbox")
        if not (isinstance(current_bbox, list) and isinstance(next_bbox, list) and len(current_bbox) == 4 and len(next_bbox) == 4):
            return False

        gap = float(next_bbox[1]) - float(current_bbox[3])
        if gap < -2:
            return False

        current_height = max(float(current_bbox[3]) - float(current_bbox[1]), 1.0)
        next_height = max(float(next_bbox[3]) - float(next_bbox[1]), 1.0)
        allowed_gap = max(18.0, min(current_height, next_height) * 1.6)
        if gap > allowed_gap:
            return False

        left_aligned = abs(float(current_bbox[0]) - float(next_bbox[0])) <= 18.0
        width_similar = abs(float(current_bbox[2]) - float(next_bbox[2])) <= 48.0
        if not left_aligned and not width_similar:
            return False

        current_text = current.text.strip()
        next_text = next_node.text.strip()
        if not current_text or not next_text:
            return False
        if self._looks_like_heading(next_text):
            return False
        if self.list_re.match(next_text):
            return False
        if self._ends_like_complete_paragraph(current_text):
            return False
        return True

    def _join_text(self, current_text: str, next_text: str) -> str:
        current = current_text.rstrip()
        next_value = next_text.lstrip()
        if not current:
            return next_value
        if not next_value:
            return current
        if current.endswith("-") and next_value[:1].isalnum():
            return current[:-1] + next_value
        if self._is_cjk(current[-1]) and self._is_cjk(next_value[0]):
            return current + next_value
        return f"{current} {next_value}"

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
        if self.list_re.match(stripped):
            return "list", 0
        if self._looks_like_heading(stripped):
            return "title", 1
        return "paragraph", 0

    def _looks_like_heading(self, text: str) -> bool:
        first_line = text.splitlines()[0].strip()
        if not first_line or len(text.splitlines()) != 1:
            return False
        if self.list_re.match(first_line):
            return False
        if len(first_line) > 28:
            return False
        if re.search(r"[@|]|\d{4}\.\d{2}|\d{11}", first_line):
            return False
        if re.search(r"[。！？!?；;，,]", first_line):
            return False
        if first_line.count(" ") > 3:
            return False
        if self.numbered_heading_re.match(first_line):
            return True
        return len(first_line) <= 12

    def _ends_like_complete_paragraph(self, text: str) -> bool:
        stripped = text.rstrip()
        if not stripped:
            return False
        return bool(re.search(r"[。！？!?；;：:]$", stripped))

    def _is_cjk(self, char: str) -> bool:
        return "\u4e00" <= char <= "\u9fff"

    def _union_bbox(self, left: object, right: object) -> list[float] | None:
        if not (isinstance(left, list) and isinstance(right, list) and len(left) == 4 and len(right) == 4):
            return left if isinstance(left, list) else right if isinstance(right, list) else None
        return [
            min(float(left[0]), float(right[0])),
            min(float(left[1]), float(right[1])),
            max(float(left[2]), float(right[2])),
            max(float(left[3]), float(right[3])),
        ]

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
