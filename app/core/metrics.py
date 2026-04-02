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

TOKEN_COUNT_COUNTER = Counter(
    "document_cutter_token_count_calls_total",
    "Token counting call counts",
    ["provider", "result"],
)

TOKEN_COUNT_DURATION = Histogram(
    "document_cutter_token_count_duration_seconds",
    "Token counting duration in seconds",
    ["provider"],
)

OVERLAP_COUNTER = Counter(
    "document_cutter_overlap_hits_total",
    "Overlap application counts during chunk splitting",
)

RECURSIVE_SPLIT_DEPTH = Histogram(
    "document_cutter_recursive_split_depth",
    "Observed recursive split depth",
)

PDF_IMAGE_REGION_DETECTED = Counter(
    "pdf_image_region_detected_total",
    "Detected PDF image regions before local vision parsing",
)

PDF_IMAGE_REGION_VISION_SUCCESS = Counter(
    "pdf_image_region_vision_success_total",
    "Successful local vision parses for PDF image regions",
)

PDF_IMAGE_REGION_VISION_ERROR = Counter(
    "pdf_image_region_vision_error_total",
    "Failed local vision parses for PDF image regions",
)


def metrics_payload() -> tuple[bytes, str]:
    return generate_latest(), CONTENT_TYPE_LATEST
