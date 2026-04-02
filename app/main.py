from __future__ import annotations

"""服务入口，负责组装路由、中间件和指标端点。"""

import logging
import time
import uuid

from fastapi import FastAPI
from fastapi.responses import Response

from app.api.routes import router
from app.core.config import settings
from app.core.logging import configure_logging, request_id_var
from app.core.metrics import REQUEST_COUNTER, REQUEST_DURATION, metrics_payload
from app.core.rate_limit import InMemoryRateLimiter


configure_logging()
logger = logging.getLogger(__name__)
rate_limiter = InMemoryRateLimiter(
    limit=settings.rate_limit_requests,
    window_seconds=settings.rate_limit_window_seconds,
)


app = FastAPI(title=settings.app_name, debug=settings.debug)


@app.middleware("http")
async def request_context_middleware(request, call_next):
    # 为每个请求注入 request_id，并在统一出口记录耗时和指标。
    request_id = request.headers.get("x-request-id", str(uuid.uuid4()))
    token = request_id_var.set(request_id)
    start = time.perf_counter()
    path = request.url.path
    client_ip = request.client.host if request.client else "unknown"
    try:
        if path not in {"/health", "/metrics"} and not rate_limiter.allow(client_ip):
            response = Response(status_code=429, content="rate limit exceeded")
        else:
            response = await call_next(request)
        response.headers["x-request-id"] = request_id
        return response
    finally:
        duration = time.perf_counter() - start
        status_code = getattr(locals().get("response"), "status_code", 500)
        REQUEST_COUNTER.labels(request.method, path, str(status_code)).inc()
        REQUEST_DURATION.labels(request.method, path).observe(duration)
        logger.info("%s %s -> %s %.3fs", request.method, path, status_code, duration)
        request_id_var.reset(token)


@app.get("/metrics", include_in_schema=False)
def metrics() -> Response:
    # 监控系统直接抓取这里暴露的原始指标文本。
    payload, content_type = metrics_payload()
    return Response(content=payload, media_type=content_type)


app.include_router(router)
