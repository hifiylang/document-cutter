from __future__ import annotations
"""Vision understanding and OCR fallback helpers."""

import base64
import json
import mimetypes
import re
import time
import uuid

from app.core.config import settings
from app.core.errors import OcrRequiredError
from app.core.metrics import EXTERNAL_CALL_COUNTER, EXTERNAL_CALL_DURATION
from app.models.schemas import DocumentNode

try:
    import fitz
except Exception:  # pragma: no cover
    fitz = None  # type: ignore

try:
    from openai import OpenAI
except Exception:  # pragma: no cover
    OpenAI = None  # type: ignore


class VisualDocumentAnalyzer:
    def __init__(self) -> None:
        self.enabled = bool(OpenAI and settings.openai_api_key and (settings.vision_model or settings.llm_model))
        base_url = settings.openai_base_url or None
        self.client = OpenAI(api_key=settings.openai_api_key, base_url=base_url) if self.enabled else None

    def analyze_image_bytes(self, file_bytes: bytes, filename: str, page_no: int | None = None) -> list[DocumentNode]:
        if not self.enabled:
            raise OcrRequiredError("vision backend is not configured for image or scanned document parsing")

        mime_type = mimetypes.guess_type(filename)[0] or "image/png"
        raw = self._run_vision_prompt(self._default_prompt(), self._to_data_url(file_bytes, mime_type))
        nodes = self._parse_nodes_from_response(
            raw,
            page_no=page_no,
            source_defaults={"modality": "vision", "parser_strategy": "vision"},
        )
        if nodes:
            return nodes
        raise OcrRequiredError("vision model did not return usable content")

    def analyze_cropped_region(
        self,
        file_bytes: bytes,
        filename: str,
        page_no: int,
        bbox: list[float],
        image_region_id: str,
    ) -> list[DocumentNode]:
        if not self.enabled:
            raise OcrRequiredError("vision backend is not configured for image region parsing")

        mime_type = mimetypes.guess_type(filename)[0] or "image/png"
        prompt = (
            "You are a document image-region extraction assistant. "
            "Extract readable content from this cropped region and return JSON only. "
            'Use the schema {"nodes":[{"node_type":"title|paragraph|table|list","text":"...","level":0,"bbox":[0,0,0,0],"layout_role":"image_region","order":0}]}. '
            "Only extract visible document content from the crop. Preserve tables and lists when possible."
        )
        raw = self._run_vision_prompt(prompt, self._to_data_url(file_bytes, mime_type))
        return self._parse_nodes_from_response(
            raw,
            page_no=page_no,
            source_defaults={
                "modality": "image",
                "parser_strategy": "vision_image_region",
                "bbox": bbox,
                "layout_role": "image_region",
                "image_region_id": image_region_id,
                "page_layout": f"page_{page_no}",
            },
        )

    def analyze_pdf_bytes(self, file_bytes: bytes, filename: str) -> list[DocumentNode]:
        if fitz is None:
            raise OcrRequiredError("pymupdf is required for scanned PDF OCR fallback")
        if not self.enabled:
            raise OcrRequiredError("vision backend is not configured for scanned PDF parsing")

        document = fitz.open(stream=file_bytes, filetype="pdf")
        nodes: list[DocumentNode] = []
        max_pages = min(len(document), settings.vision_pdf_max_pages)
        for page_index in range(max_pages):
            page = document[page_index]
            pixmap = page.get_pixmap(matrix=fitz.Matrix(2, 2), alpha=False)
            page_nodes = self.analyze_image_bytes(
                pixmap.tobytes("png"),
                f"{filename}-page-{page_index + 1}.png",
                page_index + 1,
            )
            nodes.extend(page_nodes)
        return nodes

    def _default_prompt(self) -> str:
        return (
            "You are a document understanding assistant. Extract readable content from the image and return JSON only. "
            'Use the schema {"nodes":[{"node_type":"title|paragraph|table|list","text":"...","level":0,"bbox":[0,0,0,0],"layout_role":"body","order":0}]}. '
            "Preserve natural structure. For tables, return row-based text separated by newlines and pipes. "
            "If available, keep layout hints like bbox, order and role. Do not include commentary outside JSON."
        )

    def _run_vision_prompt(self, prompt: str, image_data_url: str) -> str:
        model_name = settings.vision_model or settings.llm_model
        start = time.perf_counter()
        try:
            response = self.client.chat.completions.create(
                model=model_name,
                messages=[
                    {
                        "role": "user",
                        "content": [
                            {"type": "text", "text": prompt},
                            {"type": "image_url", "image_url": {"url": image_data_url}},
                        ],
                    }
                ],
                temperature=0.1,
            )
            EXTERNAL_CALL_COUNTER.labels("vision", "success").inc()
            return response.choices[0].message.content or ""
        except Exception:
            EXTERNAL_CALL_COUNTER.labels("vision", "error").inc()
            raise
        finally:
            EXTERNAL_CALL_DURATION.labels("vision").observe(time.perf_counter() - start)

    def _parse_nodes_from_response(
        self,
        content: str,
        page_no: int | None,
        source_defaults: dict[str, object],
    ) -> list[DocumentNode]:
        payload = self._extract_json_object(content)
        if not payload:
            return []
        parsed = json.loads(payload)
        nodes = parsed.get("nodes") or []
        results: list[DocumentNode] = []
        for item in nodes:
            node_type = item.get("node_type", "paragraph")
            if node_type not in {"title", "paragraph", "table", "list"}:
                node_type = "paragraph"
            text = (item.get("text") or "").strip()
            if not text:
                continue
            source_meta = dict(source_defaults)
            if isinstance(item.get("bbox"), list):
                source_meta["bbox"] = item.get("bbox")
            if item.get("layout_role"):
                source_meta["layout_role"] = item.get("layout_role")
            if item.get("order") is not None:
                source_meta["order"] = item.get("order")
            if page_no is not None:
                source_meta.setdefault("page_layout", f"page_{page_no}")
            results.append(
                DocumentNode(
                    node_id=str(uuid.uuid4()),
                    node_type=node_type,
                    level=max(int(item.get("level") or 0), 0),
                    text=text,
                    source_page=page_no,
                    source_meta=source_meta,
                )
            )
        return results

    def _extract_json_object(self, content: str) -> str | None:
        content = content.strip()
        if not content:
            return None
        fenced = re.search(r"```(?:json)?\s*(\{.*\})\s*```", content, re.DOTALL)
        if fenced:
            return fenced.group(1)
        direct = re.search(r"\{.*\}", content, re.DOTALL)
        if direct:
            return direct.group(0)
        return None

    def _to_data_url(self, file_bytes: bytes, mime_type: str) -> str:
        encoded = base64.b64encode(file_bytes).decode("ascii")
        return f"data:{mime_type};base64,{encoded}"
