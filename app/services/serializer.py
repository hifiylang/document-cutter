from __future__ import annotations
"""把内部块结果序列化成对外统一的 ChunkResponse。"""

import uuid
from pathlib import Path

from app.models.schemas import Chunk, ChunkResponse, DocumentNode
from app.services.token_counter import TokenCounter


class ChunkSerializer:
    strategy_version = "v2"

    def __init__(self, token_counter: TokenCounter) -> None:
        self.token_counter = token_counter

    def serialize(
        self,
        filename: str,
        blocks: list[list[DocumentNode]],
        response_metadata: dict[str, object] | None = None,
    ) -> ChunkResponse:
        """聚合块级元信息，并输出最终响应结构。"""
        chunks: list[Chunk] = []
        parser_type = Path(filename).suffix.lower().lstrip(".") or "text"
        for block in blocks:
            if not block:
                continue
            text = "\n".join(node.text for node in block if node.text).strip()
            if not text:
                continue

            section_path = self._section_path(block)
            title = section_path[-1] if section_path else None
            page_no = [node.source_page for node in block if node.source_page is not None]
            chunk_type = self._chunk_type(block)
            modalities = sorted({node.source_meta.get("modality") for node in block if node.source_meta.get("modality")})
            sheet_names = sorted({node.source_meta.get("sheet_name") for node in block if node.source_meta.get("sheet_name")})
            merge_strategy = next((node.source_meta.get("merge_strategy") for node in block if node.source_meta.get("merge_strategy")), None)
            similarity_score = next((node.source_meta.get("similarity_score") for node in block if node.source_meta.get("similarity_score") is not None), None)
            parser_strategy = sorted({node.source_meta.get("parser_strategy") for node in block if node.source_meta.get("parser_strategy")})
            offsets = self._collect_offsets(block)
            source_spans = self._collect_source_spans(block)
            token_count = self.token_counter.count(text)
            chunks.append(
                Chunk(
                    chunk_id=str(uuid.uuid4()),
                    text=text,
                    char_count=len(text),
                    token_estimate=token_count,
                    source_node_ids=[node.node_id for node in block],
                    section_path=section_path,
                    metadata={
                        "chunk_type": chunk_type,
                        "title": title,
                        "page_no": sorted(set(page_no)),
                        "parser_type": parser_type,
                        "strategy_version": self.strategy_version,
                        "modality": modalities or None,
                        "sheet_name": sheet_names or None,
                        "merge_strategy": merge_strategy,
                        "similarity_score": similarity_score,
                        "parser_strategy": parser_strategy or None,
                        "token_count": token_count,
                        "offsets": offsets or None,
                        "source_spans": source_spans or None,
                    },
                )
            )
        return ChunkResponse(
            document_id=str(uuid.uuid4()),
            filename=filename,
            total_nodes=sum(len(block) for block in blocks),
            total_chunks=len(chunks),
            chunks=chunks,
            metadata=response_metadata or {},
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

    def _collect_offsets(self, block: list[DocumentNode]) -> list[dict[str, int]]:
        offsets: list[dict[str, int]] = []
        for node in block:
            start = node.source_meta.get("char_start")
            end = node.source_meta.get("char_end")
            if isinstance(start, int) and isinstance(end, int):
                offsets.append({"start": start, "end": end})
        return offsets

    def _collect_source_spans(self, block: list[DocumentNode]) -> list[dict[str, object]]:
        spans: list[dict[str, object]] = []
        for node in block:
            span: dict[str, object] = {"node_id": node.node_id}
            if node.source_page is not None:
                span["page_no"] = node.source_page
            if node.source_meta.get("bbox") is not None:
                span["bbox"] = node.source_meta.get("bbox")
            if node.source_meta.get("layout_role") is not None:
                span["layout_role"] = node.source_meta.get("layout_role")
            if node.source_meta.get("image_region_id") is not None:
                span["image_region_id"] = node.source_meta.get("image_region_id")
            if node.source_meta.get("modality") is not None:
                span["modality"] = node.source_meta.get("modality")
            if node.source_meta.get("sheet_name") is not None:
                span["sheet_name"] = node.source_meta.get("sheet_name")
            if isinstance(node.source_meta.get("char_start"), int) and isinstance(node.source_meta.get("char_end"), int):
                span["start"] = node.source_meta.get("char_start")
                span["end"] = node.source_meta.get("char_end")
            spans.append(span)
        return spans
