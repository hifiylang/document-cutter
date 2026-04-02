from __future__ import annotations
"""Composable chunking core used by the document pipeline."""

from app.models.schemas import ChunkOptions, DocumentNode
from app.services.boundary import BoundaryDecisionEngine
from app.services.merger import ChunkMerger
from app.services.segmenter import SemanticSegmenter
from app.services.splitter import ChunkSplitter
from app.services.token_counter import TokenCounter


class TextChunker:
    """Pure chunking core: structure split -> merge -> recursive split -> boundary refine."""

    def __init__(self, token_counter: TokenCounter) -> None:
        self.segmenter = SemanticSegmenter()
        self.merger = ChunkMerger(token_counter)
        self.splitter = ChunkSplitter(token_counter)
        self.boundary_engine = BoundaryDecisionEngine(token_counter)

    def chunk(self, nodes: list[DocumentNode], options: ChunkOptions) -> list[list[DocumentNode]]:
        blocks = self.segmenter.segment(nodes)
        blocks = self.merger.merge(blocks, options)
        blocks = self.splitter.split(blocks, options)
        return self.boundary_engine.refine_blocks(blocks, options)
