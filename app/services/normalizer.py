from __future__ import annotations
"""对解析结果做轻量清洗，减少噪声对后续切分的干扰。"""

import re

from app.models.schemas import DocumentNode


class DocumentNormalizer:
    noise_re = re.compile(r"\s+")

    def normalize(self, nodes: list[DocumentNode]) -> list[DocumentNode]:
        normalized: list[DocumentNode] = []
        for node in nodes:
            text = self._normalize_text(node.text, node.node_type)
            if not text:
                continue
            updated = node.model_copy()
            updated.text = text
            if updated.node_type == "title":
                # 标题层级最少为 1，避免后续 section_path 计算出现异常。
                updated.level = max(updated.level, 1)
            normalized.append(updated)
        return normalized

    def _normalize_text(self, text: str, node_type: str) -> str:
        if node_type == "table":
            lines = [line.strip() for line in text.splitlines() if line.strip()]
            return "\n".join(lines).strip()
        lines = [self.noise_re.sub(" ", line).strip() for line in text.splitlines()]
        lines = [line for line in lines if line]
        return "\n".join(lines).strip()
