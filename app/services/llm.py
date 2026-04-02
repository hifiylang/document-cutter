from __future__ import annotations

"""LLM 只负责灰区边界裁决，不参与全文重切。"""

import json
import time

from app.core.config import settings
from app.core.metrics import EXTERNAL_CALL_COUNTER, EXTERNAL_CALL_DURATION
from app.models.schemas import DocumentNode
from app.services.model_client import ModelClient
from app.services.prompt_store import get_prompt


class LlmBoundaryRefiner:
    """相邻块的 LLM 边界裁决器。"""

    def __init__(self) -> None:
        self.enabled = bool(settings.llm_enabled and settings.openai_api_key)
        self.client = ModelClient() if self.enabled else None
        # 简单文本任务优先走 flash 小模型；如果没单独配置，再回退到文本模型。
        self.text_model = settings.text_model
        self.flash_model = settings.flash_model or settings.text_model

    def decide_merge(self, left_text: str, right_text: str) -> bool:
        """判断两个相邻文本块是否应该合并。"""
        if not self.enabled or not self.client or not self.flash_model:
            return False

        prompt = get_prompt("llm", "boundary_merge_system")
        payload = {"left": left_text[:1200], "right": right_text[:1200]}
        start = time.perf_counter()
        try:
            raw = self.client.create_text_json(
                model=self.flash_model,
                system_prompt=prompt,
                user_payload=payload,
                temperature=0.1,
                # flash 小模型默认不开启 thinking/reasoning。
                enable_thinking=False,
            )
            result = json.loads(raw)
            EXTERNAL_CALL_COUNTER.labels("llm", "success").inc()
            return result.get("decision") == "merge"
        except Exception:
            EXTERNAL_CALL_COUNTER.labels("llm", "error").inc()
            return False
        finally:
            EXTERNAL_CALL_DURATION.labels("llm").observe(time.perf_counter() - start)

    def refine_blocks(self, blocks: list[list[DocumentNode]]) -> list[list[DocumentNode]]:
        """对相邻块做灰区裁决；LLM 失败时直接回退原结果。"""
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
        """只允许同章节、非标题、非表格的相邻块进入 LLM 裁决。"""
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
        """取块里最近的章节路径，用于判断是否属于同一语义范围。"""
        for node in reversed(block):
            section_path = node.source_meta.get("section_path")
            if section_path:
                return list(section_path)
        return []
