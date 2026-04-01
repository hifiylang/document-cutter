from __future__ import annotations
"""对超长块做二次拆分，优先尊重句边界。"""

import re

from app.models.schemas import ChunkOptions, DocumentNode


class ChunkSplitter:
    sentence_boundary_re = re.compile(r"(?<=[。！？；;.!?])\s+|\n+")

    def split(self, blocks: list[list[DocumentNode]], options: ChunkOptions) -> list[list[DocumentNode]]:
        result: list[list[DocumentNode]] = []
        for block in blocks:
            if self._char_count(block) <= options.max_chunk_chars:
                result.append(block)
                continue

            # 单段正文和多节点块拆分策略不同，单段正文优先按句切。
            if len(block) == 1 and block[0].node_type == "paragraph":
                result.extend(self._split_single_node(block[0], options))
            else:
                result.extend(self._split_multi_node(block, options))
        return result

    def _split_single_node(self, node: DocumentNode, options: ChunkOptions) -> list[list[DocumentNode]]:
        sentences = self._split_sentences(node.text)
        chunks: list[list[DocumentNode]] = []
        current_parts: list[str] = []
        current_length = 0

        for sentence in sentences:
            if len(sentence) > options.max_chunk_chars:
                hard_parts = self._hard_wrap_sentence(sentence, options.max_chunk_chars)
            else:
                hard_parts = [sentence]

            for part in hard_parts:
                part_length = len(part)
                if current_parts and current_length + part_length > options.max_chunk_chars:
                    chunks.append([self._clone_with_overlap(node, current_parts)])
                    # overlap 只给被动拆开的长段落，帮助后续检索保留一点上下文。
                    overlap = current_parts[-1][-options.overlap_chars :] if options.overlap_chars else ""
                    current_parts = [overlap, part] if overlap else [part]
                    current_length = sum(len(piece) for piece in current_parts)
                    continue
                current_parts.append(part)
                current_length += part_length

        if current_parts:
            chunks.append([self._clone_with_overlap(node, current_parts)])
        return chunks

    def _split_multi_node(self, block: list[DocumentNode], options: ChunkOptions) -> list[list[DocumentNode]]:
        parts: list[list[DocumentNode]] = []
        current: list[DocumentNode] = []
        current_length = 0
        for node in block:
            node_length = len(node.text)
            if node_length > options.max_chunk_chars and node.node_type == "paragraph":
                if current:
                    parts.append(current)
                    current = []
                    current_length = 0
                parts.extend(self._split_single_node(node, options))
                continue
            if current and current_length + node_length > options.max_chunk_chars:
                parts.append(current)
                current = [node]
                current_length = node_length
                continue
            current.append(node)
            current_length += node_length
        if current:
            parts.append(current)
        return parts

    def _split_sentences(self, text: str) -> list[str]:
        parts = [part.strip() for part in self.sentence_boundary_re.split(text) if part.strip()]
        return parts or [text.strip()]

    def _hard_wrap_sentence(self, sentence: str, max_chars: int) -> list[str]:
        words = sentence.split()
        if len(words) <= 1:
            return [sentence[i : i + max_chars].strip() for i in range(0, len(sentence), max_chars) if sentence[i : i + max_chars].strip()]

        parts: list[str] = []
        current_words: list[str] = []
        current_length = 0
        for word in words:
            if current_words and current_length + 1 + len(word) > max_chars:
                parts.append(" ".join(current_words))
                current_words = [word]
                current_length = len(word)
                continue
            current_words.append(word)
            current_length = current_length + len(word) + (1 if len(current_words) > 1 else 0)
        if current_words:
            parts.append(" ".join(current_words))
        return parts

    def _clone_with_overlap(self, node: DocumentNode, parts: list[str]) -> DocumentNode:
        text = " ".join(part for part in parts if part).strip()
        updated = node.model_copy()
        updated.text = text
        return updated

    def _char_count(self, block: list[DocumentNode]) -> int:
        return sum(len(node.text) for node in block)
