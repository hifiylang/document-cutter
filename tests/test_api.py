from __future__ import annotations

"""API 集成测试，覆盖上传、URL、分页查询和详情查询。"""

import time
from io import BytesIO

from fastapi.testclient import TestClient
from openpyxl import Workbook

from app.core.config import settings
from app.core.errors import OcrRequiredError
from app.main import app, rate_limiter
from app.models.schemas import DocumentNode
from app.services.document_store import store
from app.services.pipeline import DocumentChunkPipeline


client = TestClient(app)


def build_xlsx_bytes() -> bytes:
    workbook = Workbook()
    sheet = workbook.active
    sheet.title = "Product Params"
    sheet.append(["Field", "Value"])
    sheet.append(["Color", "Red"])
    stream = BytesIO()
    workbook.save(stream)
    return stream.getvalue()


def reset_store() -> None:
    store.clear_all()
    rate_limiter.reset()


def test_health_returns_ok() -> None:
    response = client.get("/health")

    assert response.status_code == 200
    assert response.json() == {"status": "ok"}
    assert response.headers["x-request-id"]


def test_chunk_by_upload_persists_and_returns_summary() -> None:
    reset_store()
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
    assert set(body.keys()) == {"document_id", "filename", "status", "total_chunks"}
    assert body["filename"] == "sample.md"
    assert body["status"] == "completed"
    assert body["total_chunks"] >= 2

    list_response = client.get(f"/v1/documents/{body['document_id']}/chunks")
    assert list_response.status_code == 200
    list_body = list_response.json()
    assert list_body["document_id"] == body["document_id"]
    assert list_body["items"]
    assert set(list_body["items"][0].keys()) == {"chunk_id", "preview_text", "section_path", "metadata"}


def test_chunk_detail_returns_full_text() -> None:
    reset_store()
    response = client.post(
        "/v1/chunk/by-upload",
        files={"file": ("sample.md", b"# Title\n\nBody content here.", "text/markdown")},
    )
    document_id = response.json()["document_id"]
    list_response = client.get(f"/v1/documents/{document_id}/chunks")
    items = list_response.json()["items"]
    chunk_id = next(item["chunk_id"] for item in items if "Body content here" in item["preview_text"])

    detail_response = client.get(f"/v1/chunks/{chunk_id}")

    assert detail_response.status_code == 200
    detail = detail_response.json()
    assert detail["chunk_id"] == chunk_id
    assert detail["document_id"] == document_id
    assert "Body content here" in detail["text"]


def test_chunk_by_upload_supports_image_understanding() -> None:
    reset_store()
    original = DocumentChunkPipeline.chunk_bytes

    def fake_chunk(self: DocumentChunkPipeline, file_bytes: bytes, filename: str, options=None):
        assert filename == "sample.png"
        return original(self, b"# Image Result\n\nDetected visual text.", "sample.md", options)

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
    reset_store()
    original = DocumentChunkPipeline._download_url

    def fake_download(self: DocumentChunkPipeline, document_url: str, filename: str) -> bytes:
        assert document_url == "https://example.com/demo.md"
        assert filename == "demo.md"
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


def test_chunk_by_url_rejects_non_http_scheme() -> None:
    reset_store()
    response = client.post(
        "/v1/chunk/by-url",
        json={
            "document_url": "file:///tmp/demo.md",
            "filename": "demo.md",
        },
    )

    assert response.status_code == 502
    assert "http and https" in response.json()["detail"]


def test_chunk_by_upload_rejects_unsupported_type() -> None:
    reset_store()
    response = client.post(
        "/v1/chunk/by-upload",
        files={"file": ("sample.csv", b"a,b,c", "text/csv")},
    )

    assert response.status_code == 400
    assert "unsupported file type" in response.json()["detail"]


def test_chunk_by_url_returns_bad_gateway_when_download_fails() -> None:
    reset_store()
    original = DocumentChunkPipeline._download_url

    def fake_download(self: DocumentChunkPipeline, document_url: str, filename: str) -> bytes:
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


def test_get_document_returns_404_for_missing_document() -> None:
    reset_store()
    response = client.get("/v1/documents/not-exists")
    assert response.status_code == 404


def test_metrics_exposes_http_counters() -> None:
    client.get("/health")
    response = client.get("/metrics")

    assert response.status_code == 200
    assert "document_cutter_http_requests_total" in response.text


def test_rate_limit_returns_429_when_exceeded() -> None:
    reset_store()
    original = DocumentChunkPipeline._download_url

    def fake_download(self: DocumentChunkPipeline, document_url: str, filename: str) -> bytes:
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
    reset_store()
    original = DocumentChunkPipeline.chunk_bytes
    original_timeout = settings.request_timeout_seconds

    def slow_chunk(self: DocumentChunkPipeline, file_bytes: bytes, filename: str, options=None):
        time.sleep(0.2)
        return original(self, file_bytes, filename, options)

    settings.request_timeout_seconds = 0.1
    DocumentChunkPipeline.chunk_bytes = slow_chunk
    response = client.post(
        "/v1/chunk/by-upload",
        files={"file": ("slow.md", b"# Title\n\nbody", "text/markdown")},
    )
    DocumentChunkPipeline.chunk_bytes = original
    settings.request_timeout_seconds = original_timeout

    assert response.status_code == 504


def test_chunk_by_upload_returns_422_when_ocr_is_required() -> None:
    reset_store()
    original = DocumentChunkPipeline.chunk_bytes

    def no_text(self: DocumentChunkPipeline, file_bytes: bytes, filename: str, options=None):
        raise OcrRequiredError("ocr required for scanned or image-only document")

    DocumentChunkPipeline.chunk_bytes = no_text
    response = client.post(
        "/v1/chunk/by-upload",
        files={"file": ("scan.pdf", b"%PDF-1.4", "application/pdf")},
    )
    DocumentChunkPipeline.chunk_bytes = original

    assert response.status_code == 422


def test_chunk_by_upload_supports_xlsx_table_extraction() -> None:
    reset_store()
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
    document_id = response.json()["document_id"]
    list_body = client.get(f"/v1/documents/{document_id}/chunks").json()
    assert any(item["metadata"]["chunk_type"] == "table" for item in list_body["items"])


def test_list_chunks_returns_preview_text_only() -> None:
    reset_store()
    response = client.post(
        "/v1/chunk/by-upload",
        files={"file": ("sample.md", b"# Title\n\nA" * 100, "text/markdown")},
    )
    document_id = response.json()["document_id"]

    list_response = client.get(f"/v1/documents/{document_id}/chunks?page=1&page_size=5")
    body = list_response.json()

    assert list_response.status_code == 200
    assert set(body.keys()) == {"document_id", "filename", "total_chunks", "page", "page_size", "items"}
    assert set(body["items"][0].keys()) == {"chunk_id", "preview_text", "section_path", "metadata"}
