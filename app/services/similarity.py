from __future__ import annotations
"""embedding 相似度打分层。"""

import math
import time

import httpx

from app.core.config import settings
from app.core.metrics import EXTERNAL_CALL_COUNTER, EXTERNAL_CALL_DURATION


class SemanticSimilarityScorer:
    def __init__(self) -> None:
        self.enabled = bool(settings.similarity_enabled and settings.embedding_base_url)

    def score(self, left_text: str, right_text: str) -> float:
        # 相似度层只负责“像不像”，不负责最终的边界决策。
        if not self.enabled:
            raise RuntimeError("embedding similarity is not configured")

        payload = {
            "input": [left_text, right_text],
            "model": settings.embedding_model,
        }

        start = time.perf_counter()
        try:
            with httpx.Client(timeout=settings.embedding_timeout_seconds) as client:
                response = client.post(settings.embedding_base_url, json=payload, headers={"Content-Type": "application/json"})
                response.raise_for_status()
                data = response.json()
            vectors = data["data"]
            v1 = vectors[0]["embedding"]
            v2 = vectors[1]["embedding"]
            score = self._cosine_similarity(v1, v2)
            EXTERNAL_CALL_COUNTER.labels("embedding", "success").inc()
            return score
        except Exception:
            EXTERNAL_CALL_COUNTER.labels("embedding", "error").inc()
            raise
        finally:
            EXTERNAL_CALL_DURATION.labels("embedding").observe(time.perf_counter() - start)

    def _cosine_similarity(self, left: list[float], right: list[float]) -> float:
        # 这里手写余弦相似度，避免为了一个简单计算额外引入重依赖。
        if len(left) != len(right) or not left:
            raise ValueError("embedding vectors are invalid")
        numerator = sum(a * b for a, b in zip(left, right))
        left_norm = math.sqrt(sum(a * a for a in left))
        right_norm = math.sqrt(sum(b * b for b in right))
        if left_norm == 0 or right_norm == 0:
            raise ValueError("embedding norm cannot be zero")
        return float(numerator / (left_norm * right_norm))
