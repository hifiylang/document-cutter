from __future__ import annotations
"""统一封装文本和视觉模型调用，调用方只关心模型名与是否开启思考。"""

import json
from typing import Any

from app.core.config import settings

try:
    from openai import OpenAI
except Exception:  # pragma: no cover
    OpenAI = None  # type: ignore


class ModelClient:
    """OpenAI 兼容模型调用封装。"""

    def __init__(self) -> None:
        self.enabled = bool(OpenAI and settings.openai_api_key)
        base_url = settings.openai_base_url or None
        self.client = OpenAI(api_key=settings.openai_api_key, base_url=base_url) if self.enabled else None

    def create_text_json(
        self,
        *,
        model: str,
        system_prompt: str,
        user_payload: dict[str, Any],
        temperature: float = 0.1,
        enable_thinking: bool = False,
    ) -> str:
        """调用文本模型并返回原始文本结果，默认不开启思考模式。"""
        kwargs: dict[str, Any] = {
            "model": model,
            "input": [
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": json.dumps(user_payload, ensure_ascii=False)},
            ],
            "temperature": temperature,
        }
        self._apply_thinking(kwargs, enable_thinking)
        response = self.client.responses.create(**kwargs)
        return response.output_text.strip()

    def create_vision_text(
        self,
        *,
        model: str,
        prompt: str,
        image_data_url: str,
        temperature: float = 0.1,
        enable_thinking: bool = False,
    ) -> str:
        """调用视觉模型，默认也不开启思考模式。"""
        kwargs: dict[str, Any] = {
            "model": model,
            "messages": [
                {
                    "role": "user",
                    "content": [
                        {"type": "text", "text": prompt},
                        {"type": "image_url", "image_url": {"url": image_data_url}},
                    ],
                }
            ],
            "temperature": temperature,
        }
        self._apply_thinking(kwargs, enable_thinking)
        response = self.client.chat.completions.create(**kwargs)
        return response.choices[0].message.content or ""

    def _apply_thinking(self, kwargs: dict[str, Any], enable_thinking: bool) -> None:
        # 默认不传 thinking/reasoning，只有调用方明确打开时才附加。
        if not enable_thinking:
            return
        extra_body = dict(kwargs.get("extra_body") or {})
        extra_body["thinking"] = {"enabled": True}
        kwargs["extra_body"] = extra_body
