from __future__ import annotations

"""把内部块结果序列化成对外统一的 ChunkResponse。"""

import uuid
from pathlib import Path

from app.models.schemas import Chunk, ChunkMetadata, ChunkResponse, DocumentNode
from app.services.token_counter import TokenCounter


class ChunkSerializer:
    """负责聚合块级信息，并输出最小响应结构。"""

    def __init__(self, token_counter: TokenCounter) -> None:
        self.token_counter = token_counter

    def serialize(
        self,
        filename: str,
        blocks: list[list[DocumentNode]],
        response_metadata: dict[str, object] | None = None,
    ) -> ChunkResponse:
        """聚合块级信息，并输出最终响应结构。"""

        chunks: list[Chunk] = []
        _ = response_metadata
        _parser_type = Path(filename).suffix.lower().lstrip(".") or "text"
        for block in blocks:
            if not block:
                continue
            text = "\n".join(node.text for node in block if node.text).strip()
            if not text:
                continue

            section_path = self._section_path(block)
            page_no = sorted({node.source_page for node in block if node.source_page is not None})
            chunk_type = self._chunk_type(block)
            chunks.append(
                Chunk(
                    chunk_id=str(uuid.uuid4()),
                    text=text,
                    section_path=section_path,
                    metadata=ChunkMetadata(
                        chunk_type=chunk_type,
                        page_no=page_no,
                    ),
                )
            )
        return ChunkResponse(
            document_id=str(uuid.uuid4()),
            filename=filename,
            total_chunks=len(chunks),
            chunks=chunks,
        )

    def _section_path(self, block: list[DocumentNode]) -> list[str]:
        for node in reversed(block):
            section_path = node.source_meta.get("section_path")
            if section_path:
                return list(section_path)
        return []

    def _chunk_type(self, block: list[DocumentNode]) -> str:
        node_types = {node.node_type for node in block}
        if len(node_types) == 1:
            return next(iter(node_types))
        if "table" in node_types:
            return "table"
        if "list" in node_types:
            return "list"
        return "mixed"
