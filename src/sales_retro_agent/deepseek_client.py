from __future__ import annotations

import json
from typing import Any
from pathlib import Path

from .config import Settings
from .llm_debug import append_debug_record, build_debug_record


def friendly_llm_error(exc: Exception) -> Exception:
    """Turn an LLM API auth failure into an actionable message. A 401/403 from the
    provider means the key/base-url were refused. Worded as "LLM" (not a fixed
    vendor) since the engine is pluggable."""
    status = getattr(exc, "status_code", None)
    if status in (401, 403):
        return RuntimeError(
            f"LLM 鉴权失败（HTTP {status}）：请检查 LLM 的 API Key 与 Base URL 是否正确。"
        )
    return exc


class DeepSeekClient:
    def __init__(self, settings: Settings, debug_messages_path: Path | None = None):
        self.settings = settings
        self.debug_messages_path = debug_messages_path

    def complete_json(self, messages: list[dict[str, str]]) -> dict[str, Any]:
        if not self.settings.api_key:
            raise RuntimeError("未配置 LLM API Key：请在「Key 设置」中填写后重试。")

        if self.debug_messages_path:
            append_debug_record(self.debug_messages_path, build_debug_record(self.settings, messages))

        from openai import OpenAI

        client = OpenAI(api_key=self.settings.api_key, base_url=self.settings.base_url)
        try:
            response = client.chat.completions.create(
                model=self.settings.model,
                messages=messages,
                response_format={"type": "json_object"},
                max_tokens=self.settings.max_tokens,
                temperature=self.settings.temperature,
            )
        except Exception as exc:  # noqa: BLE001 - map provider auth failures to a friendly message
            raise friendly_llm_error(exc) from exc
        content = response.choices[0].message.content or ""
        return parse_json_object(content)


def parse_json_object(text: str) -> dict[str, Any]:
    cleaned = text.strip()
    if not cleaned:
        raise ValueError("LLM 返回为空。")
    if cleaned.startswith("```"):
        cleaned = cleaned.strip("`")
        cleaned = cleaned.removeprefix("json").strip()
    try:
        parsed = json.loads(cleaned)
    except json.JSONDecodeError:
        start = cleaned.find("{")
        end = cleaned.rfind("}")
        if start == -1 or end == -1 or end <= start:
            raise
        parsed = json.loads(cleaned[start : end + 1])
    if not isinstance(parsed, dict):
        raise ValueError("LLM 返回必须是 JSON 对象。")
    return parsed
