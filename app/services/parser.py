from __future__ import annotations
"""按文件类型把原始文档解析成统一的 DocumentNode。"""

import io
import re
import uuid
from abc import ABC, abstractmethod
from collections import defaultdict
from pathlib import Path

from docx import Document as DocxDocument
from openpyxl import load_workbook
from pypdf import PdfReader
import xlrd

from app.models.schemas import DocumentNode

try:
    import fitz
except Exception:  # pragma: no cover
    fitz = None  # type: ignore


IMAGE_SUFFIXES = {".png", ".jpg", ".jpeg", ".webp", ".bmp", ".tif", ".tiff"}


class BaseParser(ABC):
    @abstractmethod
    def parse(self, file_bytes: bytes, filename: str) -> list[DocumentNode]:
        raise NotImplementedError


class TxtMarkdownParser(BaseParser):
    heading_re = re.compile(r"^(#{1,6})\s+(.*)$")
    list_re = re.compile(r"^(\s*(?:[-*+]|\d+[.)]))\s+(.+)$")

    def parse(self, file_bytes: bytes, filename: str) -> list[DocumentNode]:
        # Markdown/TXT 本身带有轻量结构标记，适合直接做首轮结构识别。
        text = file_bytes.decode("utf-8", errors="ignore")
        lines = [line.rstrip() for line in text.splitlines()]
        nodes: list[DocumentNode] = []
        paragraph_buf: list[str] = []
        list_buf: list[str] = []
        in_table = False
        table_buf: list[str] = []

        def flush_paragraph() -> None:
            if not paragraph_buf:
                return
            body = "\n".join(paragraph_buf).strip()
            if body:
                nodes.append(DocumentNode(node_id=str(uuid.uuid4()), node_type="paragraph", text=body))
            paragraph_buf.clear()

        def flush_list() -> None:
            if not list_buf:
                return
            body = "\n".join(list_buf).strip()
            if body:
                nodes.append(DocumentNode(node_id=str(uuid.uuid4()), node_type="list", text=body))
            list_buf.clear()

        def flush_table() -> None:
            nonlocal in_table
            if not table_buf:
                return
            nodes.append(DocumentNode(node_id=str(uuid.uuid4()), node_type="table", text="\n".join(table_buf)))
            table_buf.clear()
            in_table = False

        for raw in lines:
            line = raw.strip()
            heading_match = self.heading_re.match(line)
            is_table_row = "|" in line and line.count("|") >= 2
            is_list_row = bool(self.list_re.match(line))

            if heading_match:
                flush_paragraph()
                flush_list()
                flush_table()
                marks, title = heading_match.groups()
                nodes.append(
                    DocumentNode(
                        node_id=str(uuid.uuid4()),
                        node_type="title",
                        level=len(marks),
                        text=title.strip(),
                    )
                )
                continue

            if is_table_row:
                flush_paragraph()
                flush_list()
                in_table = True
                table_buf.append(raw)
                continue

            if in_table and not is_table_row:
                flush_table()

            if is_list_row:
                flush_paragraph()
                list_buf.append(raw)
                continue

            if list_buf and not is_list_row:
                flush_list()

            if not line:
                flush_paragraph()
                flush_list()
                continue

            paragraph_buf.append(raw)

        flush_paragraph()
        flush_list()
        flush_table()
        return nodes


class DocxParser(BaseParser):
    def parse(self, file_bytes: bytes, filename: str) -> list[DocumentNode]:
        # Word 文档优先尊重标题样式，其次再识别普通段落和列表。
        doc = DocxDocument(io.BytesIO(file_bytes))
        nodes: list[DocumentNode] = []
        for p in doc.paragraphs:
            text = (p.text or "").strip()
            if not text:
                continue
            style_name = (p.style.name or "").lower() if p.style else ""
            if style_name.startswith("heading"):
                digits = "".join(ch for ch in style_name if ch.isdigit())
                level = int(digits) if digits else 1
                nodes.append(DocumentNode(node_id=str(uuid.uuid4()), node_type="title", level=level, text=text))
            else:
                node_type = "list" if re.match(r"^\s*(?:[-*+]|\d+[.)])\s+", text) else "paragraph"
                nodes.append(DocumentNode(node_id=str(uuid.uuid4()), node_type=node_type, text=text))

        for table in doc.tables:
            rows = []
            for row in table.rows:
                cells = [cell.text.strip() for cell in row.cells]
                if any(cells):
                    rows.append(" | ".join(cells))
            table_text = "\n".join(rows).strip()
            if table_text:
                nodes.append(DocumentNode(node_id=str(uuid.uuid4()), node_type="table", text=table_text))
        return nodes


class PdfParser(BaseParser):
    page_number_re = re.compile(r"^(?:page\s*\d+|第\s*\d+\s*页|\d+\s*/\s*\d+)$", re.IGNORECASE)

    def parse(self, file_bytes: bytes, filename: str) -> list[DocumentNode]:
        nodes: list[DocumentNode] = []
        # PDF 先走版面理解更强的解析器，失败后再回退普通文本抽取。
        if fitz is not None:
            nodes = self._extract_with_pymupdf(file_bytes)
        if not nodes:
            nodes = self._extract_with_pypdf(file_bytes)
        return self._remove_repeated_page_noise(nodes)

    def _extract_with_pymupdf(self, file_bytes: bytes) -> list[DocumentNode]:
        document = fitz.open(stream=file_bytes, filetype="pdf")
        nodes: list[DocumentNode] = []
        for page_index, page in enumerate(document, start=1):
            page_height = float(page.rect.height or 1)
            blocks = page.get_text("blocks")
            # 先按垂直位置，再按水平位置排序，尽量贴近阅读顺序。
            ordered_blocks = sorted(blocks, key=lambda item: (round(float(item[1]) / 20), float(item[0])))
            for block in ordered_blocks:
                x0, y0, x1, y1, text, *_ = block
                cleaned = self._clean_pdf_text(text)
                if not cleaned:
                    continue
                node_type, level = self._infer_pdf_node_type(cleaned)
                source_meta = {
                    "bbox": [float(x0), float(y0), float(x1), float(y1)],
                    "layout_role": self._layout_role(float(y0), float(y1), page_height),
                    "parser_strategy": "pymupdf",
                }
                nodes.append(
                    DocumentNode(
                        node_id=str(uuid.uuid4()),
                        node_type=node_type,
                        level=level,
                        text=cleaned,
                        source_page=page_index,
                        source_meta=source_meta,
                    )
                )

            nodes.extend(self._extract_tables_from_page(page, page_index))
        return nodes

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
                        source_meta={"parser_strategy": "pypdf"},
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
                # 表格天然是强语义边界，单独产出为 table 节点。
                nodes.append(
                    DocumentNode(
                        node_id=str(uuid.uuid4()),
                        node_type="table",
                        text=table_text,
                        source_page=page_index,
                        source_meta={"layout_role": "table", "parser_strategy": "pymupdf_table"},
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
        if len(stripped.splitlines()) == 1 and len(first_line) <= 60 and not re.search(r"[。.!?;:：；]", first_line):
            return "title", 1
        return "paragraph", 0

    def _layout_role(self, y0: float, y1: float, page_height: float) -> str:
        if y1 <= page_height * 0.12:
            return "header_zone"
        if y0 >= page_height * 0.88:
            return "footer_zone"
        return "body"

    def _remove_repeated_page_noise(self, nodes: list[DocumentNode]) -> list[DocumentNode]:
        by_text: dict[str, set[int]] = defaultdict(set)
        for node in nodes:
            if node.source_page is not None:
                by_text[node.text].add(node.source_page)

        repeated_texts = {
            text
            for text, pages in by_text.items()
            if len(pages) >= 2 and len(text) <= 80
        }

        cleaned: list[DocumentNode] = []
        for node in nodes:
            text = node.text.strip()
            layout_role = node.source_meta.get("layout_role")
            is_page_number = bool(self.page_number_re.match(text))
            # 重复页眉页脚和页码通常是版面噪声，会干扰后续切分和检索。
            is_repeated_margin = text in repeated_texts and layout_role in {None, "header_zone", "footer_zone"}
            if is_page_number or is_repeated_margin:
                continue
            cleaned.append(node)
        return cleaned


class XlsxParser(BaseParser):
    def parse(self, file_bytes: bytes, filename: str) -> list[DocumentNode]:
        # Excel 先按 sheet 拆，后续可直接把 sheet 看成章节边界。
        workbook = load_workbook(io.BytesIO(file_bytes), read_only=True, data_only=True)
        nodes: list[DocumentNode] = []
        for sheet in workbook.worksheets:
            rows = []
            for row in sheet.iter_rows(values_only=True):
                values = ["" if value is None else str(value).strip() for value in row]
                if any(values):
                    rows.append(" | ".join(values))
            table_text = "\n".join(rows).strip()
            if table_text:
                nodes.append(
                    DocumentNode(
                        node_id=str(uuid.uuid4()),
                        node_type="table",
                        text=table_text,
                        source_meta={"sheet_name": sheet.title, "parser_strategy": "xlsx"},
                    )
                )
        return nodes


class XlsParser(BaseParser):
    def parse(self, file_bytes: bytes, filename: str) -> list[DocumentNode]:
        workbook = xlrd.open_workbook(file_contents=file_bytes)
        nodes: list[DocumentNode] = []
        for sheet in workbook.sheets():
            rows = []
            for row_index in range(sheet.nrows):
                values = []
                for value in sheet.row_values(row_index):
                    text = "" if value is None else str(value).strip()
                    if text.endswith(".0"):
                        text = text[:-2]
                    values.append(text)
                if any(values):
                    rows.append(" | ".join(values))
            table_text = "\n".join(rows).strip()
            if table_text:
                nodes.append(
                    DocumentNode(
                        node_id=str(uuid.uuid4()),
                        node_type="table",
                        text=table_text,
                        source_meta={"sheet_name": sheet.name, "parser_strategy": "xls"},
                    )
                )
        return nodes


def get_parser(filename: str) -> BaseParser:
    lower = filename.lower()
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


def is_image_filename(filename: str) -> bool:
    return Path(filename).suffix.lower() in IMAGE_SUFFIXES
