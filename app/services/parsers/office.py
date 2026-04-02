from __future__ import annotations

"""Office 文档解析器。"""

import io
import re
import uuid

from docx import Document as DocxDocument
from openpyxl import load_workbook
import xlrd

from app.models.schemas import DocumentNode
from app.services.parsers.base import BaseParser


class DocxParser(BaseParser):
    """解析 Word 文档中的标题、正文与表格。"""

    def parse(self, file_bytes: bytes, filename: str) -> list[DocumentNode]:
        document = DocxDocument(io.BytesIO(file_bytes))
        nodes: list[DocumentNode] = []
        for paragraph in document.paragraphs:
            text = (paragraph.text or "").strip()
            if not text:
                continue
            style_name = (paragraph.style.name or "").lower() if paragraph.style else ""
            if style_name.startswith("heading"):
                digits = "".join(ch for ch in style_name if ch.isdigit())
                level = int(digits) if digits else 1
                nodes.append(
                    DocumentNode(
                        node_id=str(uuid.uuid4()),
                        node_type="title",
                        level=level,
                        text=text,
                        source_meta={"parser_strategy": "docx_heading"},
                    )
                )
            else:
                node_type = "list" if re.match(r"^\s*(?:[-*+]|\d+[.)])\s+", text) else "paragraph"
                nodes.append(
                    DocumentNode(
                        node_id=str(uuid.uuid4()),
                        node_type=node_type,
                        text=text,
                        source_meta={"parser_strategy": "docx_paragraph"},
                    )
                )

        for table in document.tables:
            rows = []
            for row in table.rows:
                cells = [cell.text.strip() for cell in row.cells]
                if any(cells):
                    rows.append(" | ".join(cells))
            table_text = "\n".join(rows).strip()
            if table_text:
                nodes.append(
                    DocumentNode(
                        node_id=str(uuid.uuid4()),
                        node_type="table",
                        text=table_text,
                        source_meta={"parser_strategy": "docx_table"},
                    )
                )
        return nodes


class XlsxParser(BaseParser):
    """按 sheet 提取 XLSX 内容。"""

    def parse(self, file_bytes: bytes, filename: str) -> list[DocumentNode]:
        workbook = load_workbook(io.BytesIO(file_bytes), read_only=True, data_only=True)
        nodes: list[DocumentNode] = []
        for sheet in workbook.worksheets:
            nodes.append(
                DocumentNode(
                    node_id=str(uuid.uuid4()),
                    node_type="title",
                    level=1,
                    text=sheet.title,
                    source_meta={"sheet_name": sheet.title, "parser_strategy": "xlsx_sheet"},
                )
            )
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
                        source_meta={"sheet_name": sheet.title, "parser_strategy": "xlsx_table"},
                    )
                )
        return nodes


class XlsParser(BaseParser):
    """按 sheet 提取 XLS 内容。"""

    def parse(self, file_bytes: bytes, filename: str) -> list[DocumentNode]:
        workbook = xlrd.open_workbook(file_contents=file_bytes)
        nodes: list[DocumentNode] = []
        for sheet in workbook.sheets():
            nodes.append(
                DocumentNode(
                    node_id=str(uuid.uuid4()),
                    node_type="title",
                    level=1,
                    text=sheet.name,
                    source_meta={"sheet_name": sheet.name, "parser_strategy": "xls_sheet"},
                )
            )
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
                        source_meta={"sheet_name": sheet.name, "parser_strategy": "xls_table"},
                    )
                )
        return nodes
