from __future__ import annotations

"""统一收口运行时的模型和 embedding 选择。"""

from dataclasses import dataclass

from app.core.config import settings
from app.models.schemas import ChunkOptions


@dataclass(frozen=True)
class RuntimeSelection:
    """一次请求实际生效的模型与服务选择。"""

    text_model: str | None
    flash_model: str | None
    vision_model: str | None
    embedding_base_url: str | None
    embedding_model: str | None
    embedding_api_key: str | None


class RuntimeSelector:
    """根据请求级参数和全局配置计算本次运行的最终选择。"""

    def resolve(self, options: ChunkOptions | None = None) -> RuntimeSelection:
        return RuntimeSelection(
            text_model=self._pick(options, "text_model", settings.text_model),
            flash_model=self._pick(options, "flash_model", settings.flash_model)
            or self._pick(options, "text_model", settings.text_model),
            vision_model=self._pick(options, "vision_model", settings.vision_model),
            embedding_base_url=self._pick(options, "embedding_base_url", settings.embedding_base_url),
            embedding_model=self._pick(options, "embedding_model", settings.embedding_model),
            embedding_api_key=self._pick(options, "embedding_api_key", settings.embedding_api_key),
        )

    def to_response_metadata(self, options: ChunkOptions | None = None) -> dict[str, object]:
        """把本次实际生效的选择转成响应可展示的结构。"""

        selected = self.resolve(options)
        return {
            "selected_options": {
                "text_model": selected.text_model,
                "flash_model": selected.flash_model,
                "vision_model": selected.vision_model,
                "embedding_base_url": selected.embedding_base_url,
                "embedding_model": selected.embedding_model,
            }
        }

    def _pick(self, options: ChunkOptions | None, field_name: str, default: str | None) -> str | None:
        if options is not None:
            value = getattr(options, field_name, None)
            if value:
                return value
        return default
