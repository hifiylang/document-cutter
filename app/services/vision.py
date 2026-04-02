from __future__ import annotations

"""视觉理解与 OCR 回退辅助能力。"""

import base64
import json
import mimetypes
import re
import time
import uuid

from app.core.config import settings
from app.core.errors import OcrRequiredError
from app.core.metrics import EXTERNAL_CALL_COUNTER, EXTERNAL_CALL_DURATION
from app.models.schemas import ChunkOptions, DocumentNode
from app.services.model_client import ModelClient
from app.services.prompt_store import get_prompt
from app.services.selection import RuntimeSelector

try:
    import fitz
except Exception:  # pragma: no cover
    fitz = None  # type: ignore


class VisualDocumentAnalyzer:
    """统一封装图片、扫描 PDF 和 PDF 局部图片区域的视觉解析。"""

    def __init__(self) -> None:
        self.enabled = bool(settings.openai_api_key and settings.vision_model)
        self.client = ModelClient() if self.enabled else None
        self.selector = RuntimeSelector()

    def analyze_image_bytes(
        self,
        file_bytes: bytes,
        filename: str,
        page_no: int | None = None,
        options: ChunkOptions | None = None,
    ) -> list[DocumentNode]:
        selection = self.selector.resolve(options)
        if not self.enabled or not selection.vision_model:
            raise OcrRequiredError("vision backend is not configured for image or scanned document parsing")

        mime_type = mimetypes.guess_type(filename)[0] or "image/png"
        raw = self._run_vision_prompt(
            model=selection.vision_model,
            prompt=get_prompt("vision", "image_understanding_prompt"),
            image_data_url=self._to_data_url(file_bytes, mime_type),
            enable_thinking=False,
        )
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
        options: ChunkOptions | None = None,
    ) -> list[DocumentNode]:
        selection = self.selector.resolve(options)
        if not self.enabled or not selection.vision_model:
            raise OcrRequiredError("vision backend is not configured for image region parsing")

        mime_type = mimetypes.guess_type(filename)[0] or "image/png"
        raw = self._run_vision_prompt(
            model=selection.vision_model,
            prompt=get_prompt("vision", "cropped_region_prompt"),
            image_data_url=self._to_data_url(file_bytes, mime_type),
            enable_thinking=False,
        )
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

    def analyze_pdf_bytes(
        self,
        file_bytes: bytes,
        filename: str,
        options: ChunkOptions | None = None,
    ) -> list[DocumentNode]:
        if fitz is None:
            raise OcrRequiredError("pymupdf is required for scanned PDF OCR fallback")

        selection = self.selector.resolve(options)
        if not self.enabled or not selection.vision_model:
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
                options,
            )
            nodes.extend(page_nodes)
        return nodes

    def _run_vision_prompt(
        self,
        *,
        model: str,
        prompt: str,
        image_data_url: str,
        enable_thinking: bool,
    ) -> str:
        start = time.perf_counter()
        try:
            raw = self.client.create_vision_text(
                model=model,
                prompt=prompt,
                image_data_url=image_data_url,
                temperature=0.1,
                enable_thinking=enable_thinking,
            )
            EXTERNAL_CALL_COUNTER.labels("vision", "success").inc()
            return raw
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
            # 模型输出先收敛到现有四类标准节点，避免下游再处理自由标签。
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
