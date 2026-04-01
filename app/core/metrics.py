from __future__ import annotations
"""Prometheus 指标定义。"""

from prometheus_client import CONTENT_TYPE_LATEST, Counter, Histogram, generate_latest


REQUEST_COUNTER = Counter(
    "document_cutter_http_requests_total",
    "Total HTTP requests handled by document cutter",
    ["method", "path", "status"],
)

REQUEST_DURATION = Histogram(
    "document_cutter_http_request_duration_seconds",
    "HTTP request duration in seconds",
    ["method", "path"],
)

BOUNDARY_DECISION_COUNTER = Counter(
    "document_cutter_boundary_decisions_total",
    "Boundary decision counts by strategy",
    ["strategy", "result"],
)

EXTERNAL_CALL_COUNTER = Counter(
    "document_cutter_external_calls_total",
    "External dependency call counts",
    ["dependency", "result"],
)

EXTERNAL_CALL_DURATION = Histogram(
    "document_cutter_external_call_duration_seconds",
    "External dependency call duration in seconds",
    ["dependency"],
)


def metrics_payload() -> tuple[bytes, str]:
    return generate_latest(), CONTENT_TYPE_LATEST
