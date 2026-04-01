from __future__ import annotations
"""API 集成测试，覆盖上传、URL、限流、超时和返回结构。"""

import time
from io import BytesIO

from fastapi.testclient import TestClient
from openpyxl import Workbook

from app.core.config import settings
from app.core.errors import OcrRequiredError
from app.main import app, rate_limiter
from app.models.schemas import DocumentNode
from app.services.pipeline import DocumentChunkPipeline
from app.services.boundary import BoundaryDecisionEngine


client = TestClient(app)


def build_xlsx_bytes() -> bytes:
    # 构造最小可用 Excel 样本，避免测试依赖外部文件。
    workbook = Workbook()
    sheet = workbook.active
    sheet.title = "Product Params"
    sheet.append(["Field", "Value"])
    sheet.append(["Color", "Red"])
    stream = BytesIO()
    workbook.save(stream)
    return stream.getvalue()


def test_health_returns_ok() -> None:
    response = client.get("/health")

    assert response.status_code == 200
    assert response.json() == {"status": "ok"}
    assert response.headers["x-request-id"]


def test_chunk_by_upload_returns_structured_chunks() -> None:
    payload = (
        "# Product Guide\n\n"
        "First paragraph introduces the product.\n"
        "Second paragraph explains the usage.\n"
        "## Parameters\n"
        "| Field | Meaning |\n"
        "| --- | --- |\n"
        "| A | Value |\n"
    )

    response = client.post(
        "/v1/chunk/by-upload",
        files={"file": ("sample.md", payload.encode("utf-8"), "text/markdown")},
    )

    assert response.status_code == 200
    body = response.json()
    assert body["filename"] == "sample.md"
    assert body["total_nodes"] >= 3
    assert body["total_chunks"] >= 2
    assert body["chunks"]
    assert all(chunk["chunk_id"] for chunk in body["chunks"])
    assert all("section_path" in chunk for chunk in body["chunks"])
    assert all("metadata" in chunk for chunk in body["chunks"])
    assert any(chunk["metadata"]["chunk_type"] == "table" for chunk in body["chunks"])


def test_chunk_by_upload_supports_image_understanding() -> None:
    original = DocumentChunkPipeline.chunk_bytes

    def fake_chunk(self: DocumentChunkPipeline, file_bytes: bytes, filename: str, options=None):
        assert filename == "sample.png"
        return original(self, b"# Image Result\n\nDetected visual text.", "sample.md", options)

    rate_limiter.reset()
    DocumentChunkPipeline.chunk_bytes = fake_chunk
    response = client.post(
        "/v1/chunk/by-upload",
        files={"file": ("sample.png", b"fake-image", "image/png")},
    )
    DocumentChunkPipeline.chunk_bytes = original

    assert response.status_code == 200
    body = response.json()
    assert body["filename"] == "sample.md"
    assert body["total_chunks"] >= 1


def test_chunk_by_url_downloads_and_processes_document() -> None:
    original = DocumentChunkPipeline._download_url

    def fake_download(self: DocumentChunkPipeline, document_url: str) -> bytes:
        assert document_url == "https://example.com/demo.md"
        return b"# Demo\n\nThis is a remote document."

    DocumentChunkPipeline._download_url = fake_download
    response = client.post(
        "/v1/chunk/by-url",
        json={
            "document_url": "https://example.com/demo.md",
            "filename": "demo.md",
        },
    )
    DocumentChunkPipeline._download_url = original

    assert response.status_code == 200
    body = response.json()
    assert body["filename"] == "demo.md"
    assert body["total_chunks"] >= 1


def test_chunk_by_upload_rejects_unsupported_type() -> None:
    response = client.post(
        "/v1/chunk/by-upload",
        files={"file": ("sample.csv", b"a,b,c", "text/csv")},
    )

    assert response.status_code == 400
    assert "unsupported file type" in response.json()["detail"]


def test_chunk_by_url_returns_bad_gateway_when_download_fails() -> None:
    original = DocumentChunkPipeline._download_url

    def fake_download(self: DocumentChunkPipeline, document_url: str) -> bytes:
        raise RuntimeError("boom")

    DocumentChunkPipeline._download_url = fake_download
    response = client.post(
        "/v1/chunk/by-url",
        json={
            "document_url": "https://example.com/missing.md",
            "filename": "missing.md",
        },
    )
    DocumentChunkPipeline._download_url = original

    assert response.status_code == 502


def test_metrics_exposes_http_counters() -> None:
    client.get("/health")
    response = client.get("/metrics")

    assert response.status_code == 200
    assert "document_cutter_http_requests_total" in response.text


def test_rate_limit_returns_429_when_exceeded() -> None:
    original = DocumentChunkPipeline._download_url

    def fake_download(self: DocumentChunkPipeline, document_url: str) -> bytes:
        return b"# Demo\n\nThis is a remote document."

    DocumentChunkPipeline._download_url = fake_download
    for _ in range(20):
        response = client.post(
            "/v1/chunk/by-url",
            json={
                "document_url": "https://example.com/limit.md",
                "filename": "limit.md",
            },
        )
    DocumentChunkPipeline._download_url = original

    assert response.status_code == 429


def test_chunk_by_upload_returns_504_when_processing_times_out() -> None:
    original = DocumentChunkPipeline.chunk_bytes
    original_timeout = settings.request_timeout_seconds

    def slow_chunk(self: DocumentChunkPipeline, file_bytes: bytes, filename: str, options=None):
        time.sleep(0.2)
        return original(self, file_bytes, filename, options)

    settings.request_timeout_seconds = 0.1
    rate_limiter.reset()
    DocumentChunkPipeline.chunk_bytes = slow_chunk
    response = client.post(
        "/v1/chunk/by-upload",
        files={"file": ("slow.md", b"# Title\n\nbody", "text/markdown")},
    )
    DocumentChunkPipeline.chunk_bytes = original
    settings.request_timeout_seconds = original_timeout

    assert response.status_code == 504


def test_chunk_by_upload_returns_422_when_ocr_is_required() -> None:
    original = DocumentChunkPipeline.chunk_bytes

    def no_text(self: DocumentChunkPipeline, file_bytes: bytes, filename: str, options=None):
        raise OcrRequiredError("ocr required for scanned or image-only document")

    rate_limiter.reset()
    DocumentChunkPipeline.chunk_bytes = no_text
    response = client.post(
        "/v1/chunk/by-upload",
        files={"file": ("scan.pdf", b"%PDF-1.4", "application/pdf")},
    )
    DocumentChunkPipeline.chunk_bytes = original

    assert response.status_code == 422


def test_chunk_by_upload_supports_xlsx_table_extraction() -> None:
    rate_limiter.reset()
    response = client.post(
        "/v1/chunk/by-upload",
        files={
            "file": (
                "sample.xlsx",
                build_xlsx_bytes(),
                "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
            )
        },
    )

    assert response.status_code == 200
    body = response.json()
    assert body["filename"] == "sample.xlsx"
    assert body["total_nodes"] >= 1
    assert any(chunk["metadata"]["chunk_type"] == "table" for chunk in body["chunks"])
    assert any("Field | Value" in chunk["text"] for chunk in body["chunks"])


def test_chunk_by_upload_exposes_similarity_metadata_when_boundary_engine_runs() -> None:
    original = BoundaryDecisionEngine.should_merge

    def fake_should_merge(self: BoundaryDecisionEngine, left_block, right_block, options):
        return {"merge": True, "strategy": "similarity_high", "similarity_score": 0.93}

    BoundaryDecisionEngine.should_merge = fake_should_merge
    rate_limiter.reset()
    payload = (
        "# Product Guide\n\n"
        "Short instruction.\n"
        "Follow-up explanation in the same section.\n"
    )
    response = client.post(
        "/v1/chunk/by-upload",
        files={"file": ("sample.md", payload.encode("utf-8"), "text/markdown")},
    )
    BoundaryDecisionEngine.should_merge = original

    assert response.status_code == 200
    body = response.json()
    merged_chunk = next(chunk for chunk in body["chunks"] if "Short instruction." in chunk["text"])
    assert merged_chunk["metadata"]["merge_strategy"] == "similarity_high"
    assert merged_chunk["metadata"]["similarity_score"] == 0.93
