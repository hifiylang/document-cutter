from __future__ import annotations

"""语义向量相似度打分层。"""

import math
import time

import httpx

from app.core.config import settings
from app.core.metrics import EXTERNAL_CALL_COUNTER, EXTERNAL_CALL_DURATION
from app.models.schemas import ChunkOptions
from app.services.selection import RuntimeSelector


class SemanticSimilarityScorer:
    """基于 embedding 服务计算相邻文本块的相似度。"""

    def __init__(self) -> None:
        self.selector = RuntimeSelector()

    def score(self, left_text: str, right_text: str, options: ChunkOptions | None = None) -> float:
        """计算两个相邻文本块的语义相似度。"""

        selection = self.selector.resolve(options)
        if not settings.similarity_enabled or not selection.embedding_base_url or not selection.embedding_model:
            raise RuntimeError("embedding similarity is not configured")

        payload = {"input": [left_text, right_text], "model": selection.embedding_model}
        start = time.perf_counter()
        try:
            with httpx.Client(timeout=settings.embedding_timeout_seconds) as client:
                response = client.post(
                    selection.embedding_base_url,
                    json=payload,
                    headers=self._build_headers(selection.embedding_api_key),
                )
                response.raise_for_status()
                data = response.json()
            vectors = data["data"]
            score = self._cosine_similarity(vectors[0]["embedding"], vectors[1]["embedding"])
            EXTERNAL_CALL_COUNTER.labels("embedding", "success").inc()
            return score
        except Exception:
            EXTERNAL_CALL_COUNTER.labels("embedding", "error").inc()
            raise
        finally:
            EXTERNAL_CALL_DURATION.labels("embedding").observe(time.perf_counter() - start)

    def _build_headers(self, api_key: str | None) -> dict[str, str]:
        """构造 embedding 服务请求头；如果配置了 key，就自动附带认证。"""

        headers = {"Content-Type": "application/json"}
        if api_key:
            headers["Authorization"] = f"Bearer {api_key}"
        return headers

    def _cosine_similarity(self, left: list[float], right: list[float]) -> float:
        """手写余弦相似度，避免为简单计算引入额外重依赖。"""

        if len(left) != len(right) or not left:
            raise ValueError("embedding vectors are invalid")
        numerator = sum(a * b for a, b in zip(left, right))
        left_norm = math.sqrt(sum(a * a for a in left))
        right_norm = math.sqrt(sum(b * b for b in right))
        if left_norm == 0 or right_norm == 0:
            raise ValueError("embedding norm cannot be zero")
        return float(numerator / (left_norm * right_norm))
