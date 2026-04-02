from __future__ import annotations

"""统一从 YAML 中加载提示词，避免提示词散落在业务代码里。"""

from functools import lru_cache
from pathlib import Path

import yaml


# 提示词文件固定放在 app 目录下，便于版本管理和统一维护。
PROMPTS_FILE = Path(__file__).resolve().parents[1] / "prompts.yml"


@lru_cache(maxsize=1)
def _load_prompts() -> dict:
    # 只在进程内加载一次，避免每次模型调用都重复读文件。
    with PROMPTS_FILE.open("r", encoding="utf-8") as file:
        data = yaml.safe_load(file) or {}
    if not isinstance(data, dict):
        raise ValueError("prompts.yml must contain a top-level mapping")
    return data


def get_prompt(*path: str) -> str:
    # 通过多级 key 读取指定提示词，例如 get_prompt("llm", "boundary_merge_system")。
    current = _load_prompts()
    for key in path:
        if not isinstance(current, dict) or key not in current:
            raise KeyError(f"prompt path not found: {'/'.join(path)}")
        current = current[key]
    if not isinstance(current, str):
        raise ValueError(f"prompt path is not a string: {'/'.join(path)}")
    return current.strip()
