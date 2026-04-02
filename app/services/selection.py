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
    """根据服务端配置计算本次运行的最终模型与 embedding 选择。"""

    def resolve(self, options: ChunkOptions | None = None) -> RuntimeSelection:
        return RuntimeSelection(
            text_model=settings.text_model,
            flash_model=settings.flash_model or settings.text_model,
            vision_model=settings.vision_model,
            embedding_base_url=settings.embedding_base_url,
            embedding_model=settings.embedding_model,
            embedding_api_key=settings.embedding_api_key,
        )

    def to_response_metadata(self, options: ChunkOptions | None = None) -> dict[str, object]:
        """把本次实际生效的服务端选择转成响应可展示的结构。"""

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
