from __future__ import annotations

"""根据标题、表格、列表等自然结构做第一轮语义切分。"""

from app.models.schemas import DocumentNode


class SemanticSegmenter:
    """保留自然结构边界，并把标题默认挂接到其后的首个内容块。"""

    def segment(self, nodes: list[DocumentNode]) -> list[list[DocumentNode]]:
        """把节点切成粗粒度结构块，供后续 token 合并和拆分使用。"""

        if not nodes:
            return []

        rough_blocks: list[list[DocumentNode]] = []
        heading_stack: list[str] = []
        pending_titles: list[DocumentNode] = []
        current_block: list[DocumentNode] = []

        def flush_current() -> None:
            if current_block:
                rough_blocks.append(current_block.copy())
                current_block.clear()

        def consume_pending_titles() -> None:
            if pending_titles:
                current_block.extend(node.model_copy(deep=True) for node in pending_titles)
                pending_titles.clear()

        for node in nodes:
            if node.node_type == "title":
                flush_current()
                level = max(node.level, 1)
                heading_stack[:] = heading_stack[: level - 1]
                heading_stack.append(node.text)

                title_node = node.model_copy(deep=True)
                title_node.source_meta = dict(title_node.source_meta)
                title_node.source_meta["section_path"] = heading_stack.copy()
                pending_titles.append(title_node)
                continue

            node_with_path = node.model_copy(deep=True)
            node_with_path.source_meta = dict(node_with_path.source_meta)
            node_with_path.source_meta["section_path"] = heading_stack.copy()

            if node.node_type == "table":
                flush_current()
                table_block: list[DocumentNode] = []
                if pending_titles:
                    table_block.extend(node.model_copy(deep=True) for node in pending_titles)
                    pending_titles.clear()
                table_block.append(node_with_path)
                rough_blocks.append(table_block)
                continue

            if pending_titles:
                consume_pending_titles()

            current_block.append(node_with_path)

            if node.node_type == "list":
                flush_current()
            elif len(node.text) > 1200:
                flush_current()

        flush_current()

        # 文档末尾若只剩孤立标题，保留为异常尾部结构，避免完全丢失章节信息。
        if pending_titles:
            rough_blocks.append([node.model_copy(deep=True) for node in pending_titles])

        return rough_blocks
