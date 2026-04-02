from __future__ import annotations

"""边界增强阶段的公共辅助方法。"""

from typing import Any

from app.models.schemas import DocumentNode
from app.services.token_counter import TokenCounter


def section_path(block: list[DocumentNode]) -> list[str]:
    """取块里最近的章节路径，用于判断语义是否仍在同一上下文。"""

    for node in reversed(block):
        value = node.source_meta.get("section_path")
        if value:
            return list(value)
    return []


def block_text(block: list[DocumentNode]) -> str:
    """把块内节点拼成连续文本，供 embedding 或 LLM 判断。"""

    return "\n".join(node.text for node in block if node.text)


def token_count(block: list[DocumentNode], counter: TokenCounter) -> int:
    """统计整块文本的 token 数。"""

    return sum(counter.count(node.text) for node in block if node.text)


def clone_block(block: list[DocumentNode]) -> list[DocumentNode]:
    """深拷贝块，避免在边界裁决过程中污染原始节点。"""

    return [node.model_copy(deep=True) for node in block]


def apply_block_metadata(block: list[DocumentNode], meta: dict[str, Any]) -> None:
    """把本轮边界决策产生的元信息回写到块内所有节点。"""

    if not meta:
        return
    for node in block:
        if meta.get("strategy"):
            node.source_meta["merge_strategy"] = meta["strategy"]
        if meta.get("similarity_score") is not None:
            node.source_meta["similarity_score"] = meta["similarity_score"]


def merge_meta(left: dict[str, Any], right: dict[str, Any]) -> dict[str, Any]:
    """把连续多次合并产生的决策元信息收敛成一份。"""

    merged = dict(left)
    if right.get("strategy"):
        merged["strategy"] = right["strategy"]
    if right.get("similarity_score") is not None:
        merged["similarity_score"] = right["similarity_score"]
    return merged
