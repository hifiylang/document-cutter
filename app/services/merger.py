from __future__ import annotations
"""Merge undersized chunks inside the same structural section."""

from app.models.schemas import ChunkOptions, DocumentNode
from app.services.token_counter import TokenCounter


class ChunkMerger:
    def __init__(self, token_counter: TokenCounter) -> None:
        self.token_counter = token_counter

    def merge(self, blocks: list[list[DocumentNode]], options: ChunkOptions) -> list[list[DocumentNode]]:
        if not blocks:
            return []

        merged: list[list[DocumentNode]] = []
        for block in blocks:
            if not block:
                continue
            candidate = [node.model_copy(deep=True) for node in block]
            if not merged:
                merged.append(candidate)
                continue

            previous = merged[-1]
            if self._can_merge(previous, candidate, options):
                previous.extend(candidate)
                continue
            merged.append(candidate)

        return merged

    def _can_merge(self, left: list[DocumentNode], right: list[DocumentNode], options: ChunkOptions) -> bool:
        if self._section_path(left) != self._section_path(right):
            return False
        left_type = self._chunk_type(left)
        right_type = self._chunk_type(right)
        if "title" in {left_type, right_type}:
            return False
        if {left_type, right_type} in ({"table", "paragraph"}, {"table", "list"}):
            return False

        right_tokens = self._token_count(right)
        if right_tokens >= options.min_chunk_tokens:
            return False

        combined_tokens = self._token_count(left) + right_tokens
        return combined_tokens <= options.max_chunk_tokens

    def _section_path(self, block: list[DocumentNode]) -> list[str]:
        for node in reversed(block):
            section_path = node.source_meta.get("section_path")
            if section_path:
                return list(section_path)
        return []

    def _token_count(self, block: list[DocumentNode]) -> int:
        return sum(self.token_counter.count(node.text) for node in block if node.text)

    def _chunk_type(self, block: list[DocumentNode]) -> str:
        if len(block) == 1:
            return block[0].node_type
        node_types = {node.node_type for node in block}
        if "table" in node_types:
            return "table"
        if "list" in node_types:
            return "list"
        return "mixed"
