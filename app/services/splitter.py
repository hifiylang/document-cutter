from __future__ import annotations
"""Token-aware recursive splitter for oversized chunk candidates."""

from dataclasses import dataclass
import math
import re

from app.core.metrics import OVERLAP_COUNTER, RECURSIVE_SPLIT_DEPTH
from app.models.schemas import ChunkOptions, DocumentNode
from app.services.token_counter import TokenCounter


@dataclass
class TextSpan:
    text: str
    start: int
    end: int


class ChunkSplitter:
    _sentence_separators = ("\n\n", "\n", "\t", " ", "。", "！", "？", ".", "!", "?", "；", ";", "，", ",", "、", "-", "_")

    def __init__(self, token_counter: TokenCounter) -> None:
        self.token_counter = token_counter

    def split(self, blocks: list[list[DocumentNode]], options: ChunkOptions) -> list[list[DocumentNode]]:
        result: list[list[DocumentNode]] = []
        for block in blocks:
            result.extend(self._split_block(block, options))
        return result

    def _split_block(self, block: list[DocumentNode], options: ChunkOptions) -> list[list[DocumentNode]]:
        if not block:
            return []
        if self._fits_budget(block, options):
            return [[node.model_copy(deep=True) for node in block]]

        if len(block) == 1 and block[0].node_type in {"paragraph", "list"}:
            return self._split_single_node(block[0], options)
        return self._split_multi_node(block, options)

    def _split_single_node(self, node: DocumentNode, options: ChunkOptions) -> list[list[DocumentNode]]:
        spans = self._recursive_split_text(node.text, options, depth=0, base_offset=0)
        spans = self._pack_spans(spans, options)
        chunks: list[list[DocumentNode]] = []
        for index, span in enumerate(spans):
            chunk_node = node.model_copy(deep=True)
            chunk_node.text = span.text
            chunk_node.source_meta = dict(chunk_node.source_meta)
            self._apply_offsets(chunk_node, span.start, span.end)
            chunks.append([chunk_node])
            if index == len(spans) - 1:
                continue
            overlap_span = self._build_overlap_span(span.text, span.end, options)
            if overlap_span is None:
                continue
            next_start = max(spans[index + 1].start - len(overlap_span.text), 0)
            if next_start < spans[index + 1].start:
                next_end = spans[index + 1].end
                candidate_text = node.text[next_start:next_end].strip()
                while candidate_text and self.token_counter.count(candidate_text) > options.max_chunk_tokens and next_start < spans[index + 1].start:
                    next_start += 1
                    candidate_text = node.text[next_start:next_end].strip()
                spans[index + 1] = TextSpan(
                    text=candidate_text,
                    start=next_start,
                    end=next_end,
                )
                OVERLAP_COUNTER.inc()
        return chunks

    def _pack_spans(self, spans: list[TextSpan], options: ChunkOptions) -> list[TextSpan]:
        if not spans:
            return []
        packed: list[TextSpan] = []
        current_text = spans[0].text
        current_start = spans[0].start
        current_end = spans[0].end
        current_tokens = self.token_counter.count(current_text)

        for span in spans[1:]:
            candidate_text = f"{current_text}{span.text}"
            candidate_tokens = self.token_counter.count(candidate_text)
            if candidate_tokens <= options.max_chunk_tokens:
                current_text = candidate_text
                current_end = span.end
                current_tokens = candidate_tokens
                continue
            packed.append(TextSpan(current_text.strip(), current_start, current_end))
            current_text = span.text
            current_start = span.start
            current_end = span.end
            current_tokens = self.token_counter.count(current_text)

        if current_text.strip():
            packed.append(TextSpan(current_text.strip(), current_start, current_end))
        return packed

    def _split_multi_node(self, block: list[DocumentNode], options: ChunkOptions) -> list[list[DocumentNode]]:
        parts: list[list[DocumentNode]] = []
        current: list[DocumentNode] = []
        current_tokens = 0
        for node in block:
            node_tokens = self.token_counter.count(node.text)
            if node.node_type in {"paragraph", "list"} and not self._fits_single_node(node, options):
                if current:
                    parts.append(current)
                    current = []
                    current_tokens = 0
                parts.extend(self._split_single_node(node, options))
                continue
            if current and current_tokens + node_tokens > options.max_chunk_tokens:
                parts.append(current)
                current = [node.model_copy(deep=True)]
                current_tokens = node_tokens
                continue
            current.append(node.model_copy(deep=True))
            current_tokens += node_tokens
        if current:
            parts.append(current)
        return parts

    def _fits_budget(self, block: list[DocumentNode], options: ChunkOptions) -> bool:
        return self._token_count(block) <= options.max_chunk_tokens

    def _fits_single_node(self, node: DocumentNode, options: ChunkOptions) -> bool:
        return self.token_counter.count(node.text) <= options.max_chunk_tokens

    def _token_count(self, block: list[DocumentNode]) -> int:
        return sum(self.token_counter.count(node.text) for node in block if node.text)

    def _recursive_split_text(self, text: str, options: ChunkOptions, depth: int, base_offset: int) -> list[TextSpan]:
        normalized = text.strip()
        if not normalized:
            return []

        RECURSIVE_SPLIT_DEPTH.observe(depth)
        if self._text_fits_budget(normalized, options):
            start = text.find(normalized)
            return [TextSpan(normalized, base_offset + start, base_offset + start + len(normalized))]

        for separator in self._sentence_separators:
            spans = self._split_by_separator(text, separator, base_offset)
            if len(spans) <= 1:
                continue
            results: list[TextSpan] = []
            for span in spans:
                results.extend(self._recursive_split_text(span.text, options, depth + 1, span.start))
            if results and all(self._text_fits_budget(span.text, options) for span in results):
                return results

        return self._hard_split(text, options, base_offset)

    def _split_by_separator(self, text: str, separator: str, base_offset: int) -> list[TextSpan]:
        spans: list[TextSpan] = []
        if separator in {"。", "！", "？", ".", "!", "?", "；", ";", "，", ",", "、", "-", "_"}:
            pattern = re.escape(separator)
            start = 0
            for match in re.finditer(pattern, text):
                end = match.end()
                chunk = text[start:end].strip()
                if chunk:
                    actual_start = text.find(chunk, start, end)
                    spans.append(TextSpan(chunk, base_offset + actual_start, base_offset + actual_start + len(chunk)))
                start = end
            tail = text[start:].strip()
            if tail:
                actual_start = text.find(tail, start)
                spans.append(TextSpan(tail, base_offset + actual_start, base_offset + actual_start + len(tail)))
            return spans

        parts = text.split(separator)
        if len(parts) <= 1:
            return []
        cursor = 0
        for index, part in enumerate(parts):
            piece = part if index == len(parts) - 1 else part + separator
            stripped = piece.strip()
            if not stripped:
                cursor += len(piece)
                continue
            actual_start = text.find(stripped, cursor)
            spans.append(TextSpan(stripped, base_offset + actual_start, base_offset + actual_start + len(stripped)))
            cursor = actual_start + len(stripped)
        return spans

    def _hard_split(self, text: str, options: ChunkOptions, base_offset: int) -> list[TextSpan]:
        # Final fallback: estimate a character window from the token budget and shrink until it fits.
        spans: list[TextSpan] = []
        approx_max_chars = max(32, options.max_chunk_tokens * 6)
        start = 0
        while start < len(text):
            end = min(start + approx_max_chars, len(text))
            candidate = text[start:end].strip()
            while candidate and self.token_counter.count(candidate) > options.max_chunk_tokens and end > start + 1:
                end -= max(1, math.ceil((end - start) * 0.1))
                candidate = text[start:end].strip()
            if not candidate:
                end = min(start + max(16, approx_max_chars // 2), len(text))
                candidate = text[start:end].strip()
            actual_start = text.find(candidate, start, end)
            spans.append(TextSpan(candidate, base_offset + actual_start, base_offset + actual_start + len(candidate)))
            start = actual_start + len(candidate)
        return spans

    def _text_fits_budget(self, text: str, options: ChunkOptions) -> bool:
        return self.token_counter.count(text) <= options.max_chunk_tokens

    def _build_overlap_span(self, text: str, end_offset: int, options: ChunkOptions) -> TextSpan | None:
        overlap_tokens = options.overlap_tokens
        if overlap_tokens <= 0 and options.overlap_ratio > 0:
            estimated = self.token_counter.count(text)
            overlap_tokens = max(1, math.floor(estimated * options.overlap_ratio))
        if overlap_tokens <= 0:
            return None
        overlap_text = self._tail_by_tokens(text, overlap_tokens)
        if not overlap_text:
            return None
        return TextSpan(overlap_text, max(end_offset - len(overlap_text), 0), end_offset)

    def _tail_by_tokens(self, text: str, max_tokens: int) -> str:
        if max_tokens <= 0:
            return ""
        start = max(0, len(text) - max_tokens * 8)
        candidate = text[start:].strip()
        while candidate and self.token_counter.count(candidate) > max_tokens and start < len(text) - 1:
            start += max(1, math.ceil((len(text) - start) * 0.1))
            candidate = text[start:].strip()
        return candidate

    def _apply_offsets(self, node: DocumentNode, local_start: int, local_end: int) -> None:
        original_start = node.source_meta.get("char_start")
        if isinstance(original_start, int):
            node.source_meta["char_start"] = original_start + local_start
            node.source_meta["char_end"] = original_start + local_end
        else:
            node.source_meta["char_start"] = local_start
            node.source_meta["char_end"] = local_end
