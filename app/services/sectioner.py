from __future__ import annotations

"""把解析节点整理为章节级批次。"""

from app.models.schemas import DocumentNode, SectionBatch


class SectionBatcher:
    """按一级/二级标题优先切分章节，标题缺失时退化为自然段簇。"""

    def batch(self, nodes: list[DocumentNode]) -> list[SectionBatch]:
        if not nodes:
            return []

        sections: list[SectionBatch] = []
        heading_stack: list[str] = []
        pending_titles: list[DocumentNode] = []
        current_nodes: list[DocumentNode] = []
        fallback_chars = 6000
        fallback_char_count = 0
        seen_top_heading = False

        def flush() -> None:
            nonlocal fallback_char_count
            if not current_nodes or self._content_count(current_nodes) == 0:
                current_nodes.clear()
                fallback_char_count = 0
                return
            sections.append(
                SectionBatch(
                    section_index=len(sections) + 1,
                    section_path=self._section_path(current_nodes),
                    nodes=[node.model_copy(deep=True) for node in current_nodes],
                )
            )
            current_nodes.clear()
            fallback_char_count = 0

        def attach_pending() -> None:
            if pending_titles:
                current_nodes.extend(node.model_copy(deep=True) for node in pending_titles)
                pending_titles.clear()

        for node in nodes:
            if node.node_type == "title":
                level = max(node.level, 1)
                heading_stack[:] = heading_stack[: level - 1]
                heading_stack.append(node.text)

                title_node = node.model_copy(deep=True)
                title_node.source_meta = dict(title_node.source_meta)
                title_node.source_meta["section_path"] = heading_stack.copy()

                if level <= 2:
                    seen_top_heading = True
                    if current_nodes:
                        flush()
                    pending_titles.append(title_node)
                    continue

                attach_pending()
                current_nodes.append(title_node)
                continue

            node_copy = node.model_copy(deep=True)
            node_copy.source_meta = dict(node_copy.source_meta)
            node_copy.source_meta.setdefault("section_path", heading_stack.copy())

            if pending_titles:
                attach_pending()

            current_nodes.append(node_copy)
            fallback_char_count += len(node_copy.text)

            if not seen_top_heading and fallback_char_count >= fallback_chars and node_copy.node_type != "table":
                flush()

        flush()
        return sections

    def _section_path(self, nodes: list[DocumentNode]) -> list[str]:
        for node in reversed(nodes):
            section_path = node.source_meta.get("section_path")
            if section_path:
                return list(section_path)
        return []

    def _content_count(self, nodes: list[DocumentNode]) -> int:
        return sum(1 for node in nodes if node.node_type != "title" and node.text.strip())
