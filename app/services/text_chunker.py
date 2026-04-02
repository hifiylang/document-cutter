from __future__ import annotations
"""供主流水线调用的可组合切分内核。"""

from app.models.schemas import ChunkOptions, DocumentNode
from app.services.boundary import BoundaryDecisionEngine
from app.services.merger import ChunkMerger
from app.services.segmenter import SemanticSegmenter
from app.services.splitter import ChunkSplitter
from app.services.token_counter import TokenCounter


class TextChunker:
    """纯切分内核：结构切分 -> 短块合并 -> 递归拆分 -> 边界增强。"""

    def __init__(self, token_counter: TokenCounter) -> None:
        self.segmenter = SemanticSegmenter()
        self.merger = ChunkMerger(token_counter)
        self.splitter = ChunkSplitter(token_counter)
        self.boundary_engine = BoundaryDecisionEngine(token_counter)

    def chunk(self, nodes: list[DocumentNode], options: ChunkOptions) -> list[list[DocumentNode]]:
        """把标准节点切成最终用于序列化的块。"""
        blocks = self.segmenter.segment(nodes)
        blocks = self.merger.merge(blocks, options)
        blocks = self.splitter.split(blocks, options)
        return self.boundary_engine.refine_blocks(blocks, options)
