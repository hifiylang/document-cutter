from __future__ import annotations
"""按最小长度规则合并过短块。"""

from app.models.schemas import ChunkOptions, DocumentNode


class ChunkMerger:
    def merge(self, blocks: list[list[DocumentNode]], options: ChunkOptions) -> list[list[DocumentNode]]:
        if not blocks:
            return []

        merged: list[list[DocumentNode]] = []
        for block in blocks:
            if not block:
                continue
            if not merged:
                merged.append(block.copy())
                continue

            current_section = self._section_path(block)
            previous_section = self._section_path(merged[-1])
            current_type = self._chunk_type(block)
            previous_type = self._chunk_type(merged[-1])
            current_len = self._char_count(block)

            if (
                current_len < options.min_chunk_chars
                and current_section == previous_section
                and "title" not in {current_type, previous_type}
                and not ({current_type, previous_type} == {"table", "paragraph"})
                and not ({current_type, previous_type} == {"table", "list"})
            ):
                # 只在同章节、非强边界类型下合并，避免为了凑长度破坏结构。
                merged[-1].extend(block)
            else:
                merged.append(block.copy())

        return merged

    def _section_path(self, block: list[DocumentNode]) -> list[str]:
        for node in block:
            section_path = node.source_meta.get("section_path")
            if section_path:
                return list(section_path)
        return []

    def _char_count(self, block: list[DocumentNode]) -> int:
        return sum(len(node.text) for node in block)

    def _chunk_type(self, block: list[DocumentNode]) -> str:
        if len(block) == 1:
            return block[0].node_type
        return "mixed"
