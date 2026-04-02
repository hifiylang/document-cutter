from __future__ import annotations

"""规则、相似度和 LLM 组成的边界决策引擎。"""

from app.core.config import settings
from app.core.metrics import BOUNDARY_DECISION_COUNTER
from app.models.schemas import ChunkOptions, DocumentNode
from app.services.boundary_rules import BoundaryRuleGate
from app.services.boundary_support import apply_block_metadata, block_text, clone_block, merge_meta
from app.services.llm import LlmBoundaryRefiner
from app.services.similarity import SemanticSimilarityScorer
from app.services.token_counter import TokenCounter


class BoundaryDecisionEngine:
    """在规则切分之后，对相邻块进行轻量边界优化。"""

    def __init__(self, token_counter: TokenCounter | None = None) -> None:
        self.token_counter = token_counter or TokenCounter()
        self.rule_gate = BoundaryRuleGate(self.token_counter)
        self.similarity_scorer = SemanticSimilarityScorer()
        self.llm_refiner = LlmBoundaryRefiner()

    def refine_blocks(self, blocks: list[list[DocumentNode]], options: ChunkOptions) -> list[list[DocumentNode]]:
        """按顺序检查相邻块，必要时合并，并把决策信息写回节点。"""

        if not blocks:
            return []

        refined: list[list[DocumentNode]] = []
        current = clone_block(blocks[0])
        current_meta: dict[str, object] = {}

        for next_block in blocks[1:]:
            decision = self.should_merge(current, next_block, options)
            if decision["merge"]:
                apply_block_metadata(current, current_meta)
                apply_block_metadata(next_block, decision)
                current.extend(clone_block(next_block))
                current_meta = merge_meta(current_meta, decision)
                continue

            apply_block_metadata(current, current_meta)
            refined.append(current)
            current = clone_block(next_block)
            current_meta = {}

        apply_block_metadata(current, current_meta)
        refined.append(current)
        return refined

    def should_merge(
        self,
        left_block: list[DocumentNode],
        right_block: list[DocumentNode],
        options: ChunkOptions,
    ) -> dict[str, object]:
        """返回两个相邻块是否应合并，以及对应策略信息。"""

        base = {"merge": False, "strategy": "rule_block", "similarity_score": None}
        if not self.rule_gate.eligible(left_block, right_block, options):
            self._record(base)
            return base

        left_text = block_text(left_block)
        right_text = block_text(right_block)

        if not settings.similarity_enabled:
            if settings.llm_enabled and self.llm_refiner.decide_merge(left_text, right_text, options):
                result = {"merge": True, "strategy": "llm_only", "similarity_score": None}
                self._record(result)
                return result
            fallback = {"merge": False, "strategy": "rule_keep", "similarity_score": None}
            self._record(fallback)
            return fallback

        try:
            score = self.similarity_scorer.score(left_text, right_text, options)
            if score >= settings.similarity_high_threshold:
                result = {"merge": True, "strategy": "similarity_high", "similarity_score": round(score, 4)}
                self._record(result)
                return result
            if score <= settings.similarity_low_threshold:
                result = {"merge": False, "strategy": "similarity_low", "similarity_score": round(score, 4)}
                self._record(result)
                return result
            if settings.llm_enabled:
                merge = self.llm_refiner.decide_merge(left_text, right_text, options)
                result = {"merge": merge, "strategy": "llm_gray", "similarity_score": round(score, 4)}
                self._record(result)
                return result
            result = {"merge": False, "strategy": "similarity_gray_keep", "similarity_score": round(score, 4)}
            self._record(result)
            return result
        except Exception:
            if settings.llm_enabled:
                merge = self.llm_refiner.decide_merge(left_text, right_text, options)
                result = {"merge": merge, "strategy": "llm_fallback", "similarity_score": None}
                self._record(result)
                return result
            self._record(base)
            return base

    def _record(self, decision: dict[str, object]) -> None:
        result = "merge" if decision.get("merge") else "keep"
        BOUNDARY_DECISION_COUNTER.labels(str(decision.get("strategy") or "unknown"), result).inc()
