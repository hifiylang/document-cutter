from __future__ import annotations
"""把内部块结构序列化成对外的 ChunkResponse。"""

import math
import uuid
from pathlib import Path

from app.models.schemas import Chunk, ChunkResponse, DocumentNode


class ChunkSerializer:
    strategy_version = "v1"

    def serialize(self, filename: str, blocks: list[list[DocumentNode]]) -> ChunkResponse:
        chunks: list[Chunk] = []
        parser_type = Path(filename).suffix.lower().lstrip(".") or "text"
        for block in blocks:
            if not block:
                continue
            text = "\n".join(node.text for node in block if node.text).strip()
            if not text:
                continue
            # metadata 会尽量保留调试和追踪信息，方便后续回溯切分来源。
            section_path = self._section_path(block)
            title = section_path[-1] if section_path else None
            page_no = [node.source_page for node in block if node.source_page is not None]
            chunk_type = self._chunk_type(block)
            modalities = sorted({node.source_meta.get("modality") for node in block if node.source_meta.get("modality")})
            sheet_names = sorted({node.source_meta.get("sheet_name") for node in block if node.source_meta.get("sheet_name")})
            merge_strategy = next((node.source_meta.get("merge_strategy") for node in block if node.source_meta.get("merge_strategy")), None)
            similarity_score = next((node.source_meta.get("similarity_score") for node in block if node.source_meta.get("similarity_score") is not None), None)
            parser_strategy = sorted({node.source_meta.get("parser_strategy") for node in block if node.source_meta.get("parser_strategy")})
            chunks.append(
                Chunk(
                    chunk_id=str(uuid.uuid4()),
                    text=text,
                    char_count=len(text),
                    token_estimate=max(1, math.ceil(len(text) / 4)),
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
                    },
                )
            )
        return ChunkResponse(
            document_id=str(uuid.uuid4()),
            filename=filename,
            total_nodes=sum(len(block) for block in blocks),
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
