from __future__ import annotations
"""LLM 只负责灰区边界裁决，不参与全文重切。"""

import json
import time

from app.core.config import settings
from app.core.metrics import EXTERNAL_CALL_COUNTER, EXTERNAL_CALL_DURATION
from app.models.schemas import DocumentNode

try:
    from openai import OpenAI
except Exception:  # pragma: no cover
    OpenAI = None  # type: ignore


class LlmBoundaryRefiner:
    def __init__(self) -> None:
        self.enabled = bool(settings.llm_enabled and settings.openai_api_key and OpenAI)
        base_url = settings.openai_base_url or None
        self.client = OpenAI(api_key=settings.openai_api_key, base_url=base_url) if self.enabled else None

    def decide_merge(self, left_text: str, right_text: str) -> bool:
        if not self.enabled:
            return False

        # 这里的提示词只要求模型做“合并/保留”二选一，避免回答发散。
        prompt = (
            "You are a document chunk boundary assistant. Decide whether two adjacent chunks should be merged. "
            'Return JSON only: {"decision":"merge"} or {"decision":"keep"}. '
            "Merge only when they clearly belong to the same semantic unit."
        )
        payload = {"left": left_text[:1200], "right": right_text[:1200]}
        start = time.perf_counter()
        try:
            response = self.client.responses.create(
                model=settings.llm_model,
                input=[
                    {"role": "system", "content": prompt},
                    {"role": "user", "content": json.dumps(payload, ensure_ascii=False)},
                ],
                temperature=0.1,
            )
            result = json.loads(response.output_text.strip())
            EXTERNAL_CALL_COUNTER.labels("llm", "success").inc()
            return result.get("decision") == "merge"
        except Exception:
            EXTERNAL_CALL_COUNTER.labels("llm", "error").inc()
            return False
        finally:
            EXTERNAL_CALL_DURATION.labels("llm").observe(time.perf_counter() - start)

    def refine_blocks(self, blocks: list[list[DocumentNode]]) -> list[list[DocumentNode]]:
        if not blocks:
            return []
        try:
            refined: list[list[DocumentNode]] = []
            current = blocks[0].copy()
            for next_block in blocks[1:]:
                if self._can_consider_merge(current, next_block):
                    left_text = "\n".join(node.text for node in current)
                    right_text = "\n".join(node.text for node in next_block)
                    if self.decide_merge(left_text, right_text):
                        current.extend(next_block)
                        continue
                refined.append(current)
                current = next_block.copy()
            refined.append(current)
            return refined
        except Exception:
            return blocks

    def _can_consider_merge(self, left_block: list[DocumentNode], right_block: list[DocumentNode]) -> bool:
        if not left_block or not right_block:
            return False
        left_types = {node.node_type for node in left_block}
        right_types = {node.node_type for node in right_block}
        if "title" in left_types or "title" in right_types:
            return False
        if "table" in left_types or "table" in right_types:
            return False
        return self._section_path(left_block) == self._section_path(right_block)

    def _section_path(self, block: list[DocumentNode]) -> list[str]:
        for node in reversed(block):
            section_path = node.source_meta.get("section_path")
            if section_path:
                return list(section_path)
        return []
