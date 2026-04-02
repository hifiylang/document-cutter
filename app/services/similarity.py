from __future__ import annotations

"""语义向量相似度打分层。"""

import math
import time

import httpx

from app.core.config import settings
from app.core.metrics import EXTERNAL_CALL_COUNTER, EXTERNAL_CALL_DURATION
from app.models.schemas import ChunkOptions


class SemanticSimilarityScorer:
    """基于 embedding 模型服务计算相邻文本块的相似度。"""

    def score(self, left_text: str, right_text: str, options: ChunkOptions | None = None) -> float:
        """计算两个相邻文本块的语义相似度。"""
        base_url = self._embedding_base_url(options)
        model = self._embedding_model(options)
        if not settings.similarity_enabled or not base_url or not model:
            raise RuntimeError("embedding similarity is not configured")

        payload = {
            "input": [left_text, right_text],
            "model": model,
        }

        start = time.perf_counter()
        try:
            with httpx.Client(timeout=settings.embedding_timeout_seconds) as client:
                response = client.post(
                    base_url,
                    json=payload,
                    headers=self._build_headers(options),
                )
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

    def _build_headers(self, options: ChunkOptions | None = None) -> dict[str, str]:
        """构造 embedding 服务请求头；如果配置了 key，就自动带上认证信息。"""
        headers = {"Content-Type": "application/json"}
        api_key = self._embedding_api_key(options)
        if api_key:
            headers["Authorization"] = f"Bearer {api_key}"
        return headers

    def _embedding_base_url(self, options: ChunkOptions | None = None) -> str | None:
        if options and options.embedding_base_url:
            return options.embedding_base_url
        return settings.embedding_base_url

    def _embedding_model(self, options: ChunkOptions | None = None) -> str | None:
        if options and options.embedding_model:
            return options.embedding_model
        return settings.embedding_model

    def _embedding_api_key(self, options: ChunkOptions | None = None) -> str | None:
        if options and options.embedding_api_key:
            return options.embedding_api_key
        return settings.embedding_api_key

    def _cosine_similarity(self, left: list[float], right: list[float]) -> float:
        """手写余弦相似度，避免为了简单计算引入额外重依赖。"""
        if len(left) != len(right) or not left:
            raise ValueError("embedding vectors are invalid")
        numerator = sum(a * b for a, b in zip(left, right))
        left_norm = math.sqrt(sum(a * a for a in left))
        right_norm = math.sqrt(sum(b * b for b in right))
        if left_norm == 0 or right_norm == 0:
            raise ValueError("embedding norm cannot be zero")
        return float(numerator / (left_norm * right_norm))
