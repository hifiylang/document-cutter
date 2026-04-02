from __future__ import annotations
"""日志初始化，保证每条日志都能带上请求标识。"""

import contextvars
import logging


request_id_var: contextvars.ContextVar[str] = contextvars.ContextVar("request_id", default="-")


class RequestIdFilter(logging.Filter):
    def filter(self, record: logging.LogRecord) -> bool:
        record.request_id = request_id_var.get()
        return True


def configure_logging() -> None:
    # 避免重复初始化导致 handler 叠加、日志重复打印。
    root_logger = logging.getLogger()
    if any(isinstance(f, RequestIdFilter) for f in root_logger.filters):
        return
    handler = logging.StreamHandler()
    handler.setFormatter(
        logging.Formatter(
            fmt="%(asctime)s %(levelname)s [request_id=%(request_id)s] %(name)s - %(message)s"
        )
    )
    handler.addFilter(RequestIdFilter())
    root_logger.handlers = [handler]
    root_logger.setLevel(logging.INFO)
    root_logger.addFilter(RequestIdFilter())
