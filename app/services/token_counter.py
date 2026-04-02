from __future__ import annotations

"""切分内核使用的统一 token 计数抽象。"""

import math
import time

import httpx

from app.core.config import settings
from app.core.metrics import TOKEN_COUNT_COUNTER, TOKEN_COUNT_DURATION


class TokenCounter:
    """统一 token 计数入口，支持本地估算和远程服务。"""

    def __init__(self) -> None:
        self.provider = settings.token_counter_provider.lower()
        self.endpoint = settings.token_counter_endpoint
        self._cache: dict[str, int] = {}
        self._max_cache_size = 2048

    def count(self, text: str) -> int:
        """对输入文本做 token 计数。"""

        normalized = text.strip()
        if not normalized:
            return 0
        if normalized in self._cache:
            return self._cache[normalized]

        start = time.perf_counter()
        if self.provider == "http" and self.endpoint:
            try:
                result = self._count_by_http(normalized)
                TOKEN_COUNT_COUNTER.labels(self.provider, "success").inc()
                self._remember(normalized, result)
                return result
            except Exception:
                TOKEN_COUNT_COUNTER.labels(self.provider, "error").inc()
                raise
            finally:
                TOKEN_COUNT_DURATION.labels(self.provider).observe(time.perf_counter() - start)

        result = self._count_by_heuristic(normalized)
        TOKEN_COUNT_COUNTER.labels(self.provider, "success").inc()
        TOKEN_COUNT_DURATION.labels(self.provider).observe(time.perf_counter() - start)
        self._remember(normalized, result)
        return result

    def _count_by_http(self, text: str) -> int:
        """调用远程 token 计数服务。"""

        payload = {"input": text}
        with httpx.Client(timeout=settings.token_counter_timeout_seconds) as client:
            response = client.post(self.endpoint, json=payload)
            response.raise_for_status()
            data = response.json()

        if isinstance(data, dict):
            if isinstance(data.get("token_count"), int):
                return data["token_count"]
            if isinstance(data.get("count"), int):
                return data["count"]
        raise ValueError("token counter response missing token count")

    def _count_by_heuristic(self, text: str) -> int:
        # 启发式估算不依赖额外模型服务，适合作为默认兜底。
        return max(1, math.ceil(len(text) / 4))

    def _remember(self, text: str, value: int) -> None:
        if len(self._cache) >= self._max_cache_size:
            self._cache.pop(next(iter(self._cache)))
        self._cache[text] = value
