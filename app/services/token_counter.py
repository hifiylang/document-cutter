from __future__ import annotations
"""Token counting abstraction used by the chunking core."""

import math
import time

import httpx

from app.core.config import settings
from app.core.metrics import TOKEN_COUNT_COUNTER, TOKEN_COUNT_DURATION


class TokenCounter:
    """Unified token counting entry point with local and remote providers."""

    def __init__(self) -> None:
        self.provider = settings.token_counter_provider.lower()
        self.endpoint = settings.token_counter_endpoint

    def count(self, text: str) -> int:
        normalized = text.strip()
        if not normalized:
            return 0

        start = time.perf_counter()
        if self.provider == "http" and self.endpoint:
            try:
                result = self._count_by_http(normalized)
                TOKEN_COUNT_COUNTER.labels(self.provider, "success").inc()
                return result
            except Exception:
                TOKEN_COUNT_COUNTER.labels(self.provider, "error").inc()
                raise
            finally:
                TOKEN_COUNT_DURATION.labels(self.provider).observe(time.perf_counter() - start)

        result = self._count_by_heuristic(normalized)
        TOKEN_COUNT_COUNTER.labels(self.provider, "success").inc()
        TOKEN_COUNT_DURATION.labels(self.provider).observe(time.perf_counter() - start)
        return result

    def _count_by_http(self, text: str) -> int:
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
        # Heuristic fallback keeps chunk budgeting stable without extra dependencies.
        return max(1, math.ceil(len(text) / 4))
