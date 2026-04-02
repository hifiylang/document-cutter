from __future__ import annotations

"""边界增强前的规则过滤。"""

from app.models.schemas import ChunkOptions, DocumentNode
from app.services.boundary_support import section_path, token_count
from app.services.token_counter import TokenCounter


class BoundaryRuleGate:
    """只让值得做语义增强的相邻块进入后续判断。"""

    def __init__(self, token_counter: TokenCounter) -> None:
        self.token_counter = token_counter

    def eligible(self, left_block: list[DocumentNode], right_block: list[DocumentNode], options: ChunkOptions) -> bool:
        if not left_block or not right_block:
            return False
        left_types = {node.node_type for node in left_block}
        right_types = {node.node_type for node in right_block}
        if "title" in left_types or "title" in right_types:
            return False
        if "table" in left_types or "table" in right_types:
            return False
        if section_path(left_block) != section_path(right_block):
            return False
        return token_count(left_block, self.token_counter) + token_count(right_block, self.token_counter) <= options.max_chunk_tokens
