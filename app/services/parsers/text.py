from __future__ import annotations

"""TXT 与 Markdown 解析器。"""

import re
import uuid

from app.models.schemas import DocumentNode
from app.services.parsers.base import BaseParser


class TxtMarkdownParser(BaseParser):
    """解析 TXT 和 Markdown，保留标题、段落、列表与表格结构。"""

    heading_re = re.compile(r"^(#{1,6})\s+(.*)$")
    list_re = re.compile(r"^(\s*(?:[-*+]|\d+[.)]))\s+(.+)$")

    def parse(self, file_bytes: bytes, filename: str) -> list[DocumentNode]:
        text = file_bytes.decode("utf-8", errors="ignore")
        lines = text.splitlines(keepends=True)
        nodes: list[DocumentNode] = []
        paragraph_buf: list[str] = []
        paragraph_start: int | None = None
        list_buf: list[str] = []
        list_start: int | None = None
        table_buf: list[str] = []
        table_start: int | None = None
        cursor = 0

        def flush_paragraph(end: int) -> None:
            nonlocal paragraph_start
            if not paragraph_buf or paragraph_start is None:
                return
            body = "".join(paragraph_buf).strip()
            if body:
                body_start = text.find(body, paragraph_start, end)
                nodes.append(
                    DocumentNode(
                        node_id=str(uuid.uuid4()),
                        node_type="paragraph",
                        text=body,
                        source_meta={
                            "char_start": body_start,
                            "char_end": body_start + len(body),
                            "parser_strategy": "markdown_text",
                        },
                    )
                )
            paragraph_buf.clear()
            paragraph_start = None

        def flush_list(end: int) -> None:
            nonlocal list_start
            if not list_buf or list_start is None:
                return
            body = "".join(list_buf).strip()
            if body:
                body_start = text.find(body, list_start, end)
                nodes.append(
                    DocumentNode(
                        node_id=str(uuid.uuid4()),
                        node_type="list",
                        text=body,
                        source_meta={
                            "char_start": body_start,
                            "char_end": body_start + len(body),
                            "parser_strategy": "markdown_text",
                        },
                    )
                )
            list_buf.clear()
            list_start = None

        def flush_table(end: int) -> None:
            nonlocal table_start
            if not table_buf or table_start is None:
                return
            body = "".join(table_buf).strip()
            if body:
                body_start = text.find(body, table_start, end)
                nodes.append(
                    DocumentNode(
                        node_id=str(uuid.uuid4()),
                        node_type="table",
                        text=body,
                        source_meta={
                            "char_start": body_start,
                            "char_end": body_start + len(body),
                            "parser_strategy": "markdown_table",
                        },
                    )
                )
            table_buf.clear()
            table_start = None

        for raw in lines:
            line_start = cursor
            cursor += len(raw)
            line = raw.rstrip("\r\n")
            stripped = line.strip()
            heading_match = self.heading_re.match(stripped)
            is_table_row = "|" in stripped and stripped.count("|") >= 2
            is_list_row = bool(self.list_re.match(stripped))

            if heading_match:
                flush_paragraph(line_start)
                flush_list(line_start)
                flush_table(line_start)
                marks, title = heading_match.groups()
                body_start = text.find(title.strip(), line_start, cursor)
                nodes.append(
                    DocumentNode(
                        node_id=str(uuid.uuid4()),
                        node_type="title",
                        level=len(marks),
                        text=title.strip(),
                        source_meta={
                            "char_start": body_start,
                            "char_end": body_start + len(title.strip()),
                            "parser_strategy": "markdown_title",
                        },
                    )
                )
                continue

            if is_table_row:
                flush_paragraph(line_start)
                flush_list(line_start)
                if table_start is None:
                    table_start = line_start
                table_buf.append(raw)
                continue

            if table_buf:
                flush_table(line_start)

            if is_list_row:
                flush_paragraph(line_start)
                if list_start is None:
                    list_start = line_start
                list_buf.append(raw)
                continue

            if list_buf:
                flush_list(line_start)

            if not stripped:
                flush_paragraph(line_start)
                continue

            if paragraph_start is None:
                paragraph_start = line_start
            paragraph_buf.append(raw)

        flush_paragraph(len(text))
        flush_list(len(text))
        flush_table(len(text))
        return nodes
