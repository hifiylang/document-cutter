from __future__ import annotations
"""视觉理解与 OCR 回退能力。"""

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
        # 图片文档和扫描件都会先被转成视觉模型可消费的输入。
        if not self.enabled:
            raise OcrRequiredError("vision backend is not configured for image or scanned document parsing")

        mime_type = mimetypes.guess_type(filename)[0] or "image/png"
        data_url = self._to_data_url(file_bytes, mime_type)
        prompt = (
            "You are a document understanding assistant. Extract readable content from the image and return JSON only. "
            'Use the schema {"nodes":[{"node_type":"title|paragraph|table|list","text":"...","level":0}]}. '
            "Preserve natural structure. For tables, return row-based text separated by newlines and pipes. "
            "Do not include commentary outside JSON."
        )
        raw = self._run_vision_prompt(prompt, data_url)
        nodes = self._parse_nodes_from_response(raw, page_no)
        if nodes:
            return nodes
        raise OcrRequiredError("vision model did not return usable content")

    def analyze_pdf_bytes(self, file_bytes: bytes, filename: str) -> list[DocumentNode]:
        if fitz is None:
            raise OcrRequiredError("pymupdf is required for scanned PDF OCR fallback")
        if not self.enabled:
            raise OcrRequiredError("vision backend is not configured for scanned PDF parsing")

        document = fitz.open(stream=file_bytes, filetype="pdf")
        nodes: list[DocumentNode] = []
        max_pages = min(len(document), settings.vision_pdf_max_pages)
        for page_index in range(max_pages):
            # 扫描 PDF 按页渲染成图片，再复用统一的图片理解逻辑。
            page = document[page_index]
            pixmap = page.get_pixmap(matrix=fitz.Matrix(2, 2), alpha=False)
            page_nodes = self.analyze_image_bytes(pixmap.tobytes("png"), f"{filename}-page-{page_index + 1}.png", page_index + 1)
            nodes.extend(page_nodes)
        return nodes

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

    def _parse_nodes_from_response(self, content: str, page_no: int | None) -> list[DocumentNode]:
        payload = self._extract_json_object(content)
        if not payload:
            return []
        parsed = json.loads(payload)
        nodes = parsed.get("nodes") or []
        results: list[DocumentNode] = []
        for item in nodes:
            # 模型输出会先收敛到固定的四类节点，避免下游再处理自由文本标签。
            node_type = item.get("node_type", "paragraph")
            if node_type not in {"title", "paragraph", "table", "list"}:
                node_type = "paragraph"
            text = (item.get("text") or "").strip()
            if not text:
                continue
            results.append(
                DocumentNode(
                    node_id=str(uuid.uuid4()),
                    node_type=node_type,
                    level=max(int(item.get("level") or 0), 0),
                    text=text,
                    source_page=page_no,
                    source_meta={"modality": "vision", "parser_strategy": "vision"},
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
