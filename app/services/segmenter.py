from __future__ import annotations

"""根据标题、表格、列表等自然结构做首轮粗切分。"""

from app.models.schemas import DocumentNode


class SemanticSegmenter:
    """保留自然结构边界，并给节点补齐章节路径。"""

    def segment(self, nodes: list[DocumentNode]) -> list[list[DocumentNode]]:
        """把节点切成粗粒度结构块，供后续 token 合并和拆分使用。"""

        if not nodes:
            return []

        rough_blocks: list[list[DocumentNode]] = []
        heading_stack: list[str] = []
        current_block: list[DocumentNode] = []

        def flush() -> None:
            if current_block:
                rough_blocks.append(current_block.copy())
                current_block.clear()

        for node in nodes:
            if node.node_type == "title":
                flush()
                # 标题一旦出现，就意味着 section_path 发生切换。
                level = max(node.level, 1)
                heading_stack[:] = heading_stack[: level - 1]
                heading_stack.append(node.text)
                title_node = node.model_copy()
                title_node.source_meta["section_path"] = heading_stack.copy()
                rough_blocks.append([title_node])
                continue

            node_with_path = node.model_copy()
            node_with_path.source_meta["section_path"] = heading_stack.copy()
            if node.node_type == "table":
                # 表格优先独立成块，避免和正文缠在一起影响后续问答抽取。
                flush()
                rough_blocks.append([node_with_path])
            else:
                current_block.append(node_with_path)
                if node.node_type == "list":
                    flush()
                elif len(node.text) > 1200:
                    flush()

        flush()
        return rough_blocks
