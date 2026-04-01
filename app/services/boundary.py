from __future__ import annotations
"""规则、相似度、LLM 三层边界决策引擎。"""

from app.core.config import settings
from app.core.metrics import BOUNDARY_DECISION_COUNTER
from app.models.schemas import ChunkOptions, DocumentNode
from app.services.llm import LlmBoundaryRefiner
from app.services.similarity import SemanticSimilarityScorer


class BoundaryDecisionEngine:
    def __init__(self) -> None:
        self.similarity_scorer = SemanticSimilarityScorer()
        self.llm_refiner = LlmBoundaryRefiner()

    def refine_blocks(self, blocks: list[list[DocumentNode]], options: ChunkOptions) -> list[list[DocumentNode]]:
        if not blocks:
            return []
        refined: list[list[DocumentNode]] = []
        current = self._clone_block(blocks[0])
        current_meta: dict[str, object] = {}

        for next_block in blocks[1:]:
            # 这里只处理相邻块，避免边界增强演化成“全文重排”。
            decision = self.should_merge(current, next_block, options)
            if decision["merge"]:
                self._apply_block_metadata(current, current_meta)
                self._apply_block_metadata(next_block, decision)
                current.extend(self._clone_block(next_block))
                current_meta = self._merge_meta(current_meta, decision)
                continue

            self._apply_block_metadata(current, current_meta)
            refined.append(current)
            current = self._clone_block(next_block)
            current_meta = {}

        self._apply_block_metadata(current, current_meta)
        refined.append(current)
        return refined

    def should_merge(self, left_block: list[DocumentNode], right_block: list[DocumentNode], options: ChunkOptions) -> dict[str, object]:
        # 默认保守：不满足条件时宁可不合并，也不要把不同主题强行拼在一起。
        base = {"merge": False, "strategy": "rule_block", "similarity_score": None}
        if not self._eligible(left_block, right_block, options):
            self._record(base)
            return base

        left_text = self._block_text(left_block)
        right_text = self._block_text(right_block)

        if not options.similarity_enabled:
            # 没开相似度时，只在显式启用 LLM 的情况下让模型兜底。
            if options.llm_enabled and self.llm_refiner.decide_merge(left_text, right_text):
                result = {"merge": True, "strategy": "llm_only", "similarity_score": None}
                self._record(result)
                return result
            fallback = {"merge": False, "strategy": "rule_keep", "similarity_score": None}
            self._record(fallback)
            return fallback

        try:
            score = self.similarity_scorer.score(left_text, right_text)
            # 高分和低分都直接拍板，只有灰区才让 LLM 参与。
            if score >= settings.similarity_high_threshold:
                result = {"merge": True, "strategy": "similarity_high", "similarity_score": round(score, 4)}
                self._record(result)
                return result
            if score <= settings.similarity_low_threshold:
                result = {"merge": False, "strategy": "similarity_low", "similarity_score": round(score, 4)}
                self._record(result)
                return result
            if options.llm_enabled:
                merge = self.llm_refiner.decide_merge(left_text, right_text)
                result = {"merge": merge, "strategy": "llm_gray", "similarity_score": round(score, 4)}
                self._record(result)
                return result
            result = {"merge": False, "strategy": "similarity_gray_keep", "similarity_score": round(score, 4)}
            self._record(result)
            return result
        except Exception:
            # embedding 服务异常时，不阻塞主链路，按配置决定是否交给 LLM 兜底。
            if options.llm_enabled:
                merge = self.llm_refiner.decide_merge(left_text, right_text)
                result = {"merge": merge, "strategy": "llm_fallback", "similarity_score": None}
                self._record(result)
                return result
            self._record(base)
            return base

    def _eligible(self, left_block: list[DocumentNode], right_block: list[DocumentNode], options: ChunkOptions) -> bool:
        if not left_block or not right_block:
            return False
        left_types = {node.node_type for node in left_block}
        right_types = {node.node_type for node in right_block}
        # 标题、表格是强边界，默认不让增强层跨过去。
        if "title" in left_types or "title" in right_types:
            return False
        if "table" in left_types or "table" in right_types:
            return False
        if self._section_path(left_block) != self._section_path(right_block):
            return False
        return self._char_count(left_block) + self._char_count(right_block) <= options.max_chunk_chars

    def _section_path(self, block: list[DocumentNode]) -> list[str]:
        for node in reversed(block):
            section_path = node.source_meta.get("section_path")
            if section_path:
                return list(section_path)
        return []

    def _block_text(self, block: list[DocumentNode]) -> str:
        return "\n".join(node.text for node in block if node.text)

    def _char_count(self, block: list[DocumentNode]) -> int:
        return sum(len(node.text) for node in block)

    def _clone_block(self, block: list[DocumentNode]) -> list[DocumentNode]:
        return [node.model_copy(deep=True) for node in block]

    def _apply_block_metadata(self, block: list[DocumentNode], meta: dict[str, object]) -> None:
        if not meta:
            return
        for node in block:
            node.source_meta.setdefault("merge_strategy", meta.get("strategy"))
            if meta.get("similarity_score") is not None:
                node.source_meta.setdefault("similarity_score", meta.get("similarity_score"))

    def _merge_meta(self, current_meta: dict[str, object], new_meta: dict[str, object]) -> dict[str, object]:
        if new_meta.get("merge"):
            return {
                "strategy": new_meta.get("strategy"),
                "similarity_score": new_meta.get("similarity_score"),
            }
        return current_meta

    def _record(self, result: dict[str, object]) -> None:
        BOUNDARY_DECISION_COUNTER.labels(str(result["strategy"]), "merge" if result["merge"] else "keep").inc()
