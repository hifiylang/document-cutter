from __future__ import annotations

"""切分内核使用的统一 token 计数抽象。"""

import math
import time
from functools import lru_cache

import httpx

from app.core.config import settings
from app.core.metrics import TOKEN_COUNT_COUNTER, TOKEN_COUNT_DURATION


@lru_cache(maxsize=4096)
def _heuristic_count(text: str) -> int:
    """默认启发式估算，不依赖额外模型服务。"""

    return max(1, math.ceil(len(text) / 4))


@lru_cache(maxsize=4096)
def _http_count(endpoint: str, timeout_seconds: float, text: str) -> int:
    """调用远程 token 计数服务，并按请求参数做缓存。"""

    payload = {"input": text}
    with httpx.Client(timeout=timeout_seconds) as client:
        response = client.post(endpoint, json=payload)
        response.raise_for_status()
        data = response.json()

    if isinstance(data, dict):
        if isinstance(data.get("token_count"), int):
            return data["token_count"]
        if isinstance(data.get("count"), int):
            return data["count"]
    raise ValueError("token counter response missing token count")


class TokenCounter:
    """统一 token 计数入口，支持本地估算和远程服务。"""

    def __init__(self) -> None:
        self.provider = settings.token_counter_provider.lower()
        self.endpoint = settings.token_counter_endpoint

    def count(self, text: str) -> int:
        """对输入文本做 token 计数。"""

        normalized = text.strip()
        if not normalized:
            return 0

        start = time.perf_counter()
        if self.provider == "http" and self.endpoint:
            try:
                result = _http_count(self.endpoint, settings.token_counter_timeout_seconds, normalized)
                TOKEN_COUNT_COUNTER.labels(self.provider, "success").inc()
                return result
            except Exception:
                TOKEN_COUNT_COUNTER.labels(self.provider, "error").inc()
                raise
            finally:
                TOKEN_COUNT_DURATION.labels(self.provider).observe(time.perf_counter() - start)

        result = _heuristic_count(normalized)
        TOKEN_COUNT_COUNTER.labels(self.provider, "success").inc()
        TOKEN_COUNT_DURATION.labels(self.provider).observe(time.perf_counter() - start)
        return result
