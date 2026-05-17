from __future__ import annotations

import json
from typing import Any
from pathlib import Path

from .config import Settings
from .llm_debug import append_debug_record, build_debug_record


class DeepSeekClient:
    def __init__(self, settings: Settings, debug_messages_path: Path | None = None):
        self.settings = settings
        self.debug_messages_path = debug_messages_path

    def complete_json(self, messages: list[dict[str, str]]) -> dict[str, Any]:
        if not self.settings.api_key:
            raise RuntimeError("Missing DEEPSEEK_API_KEY. Set it in .env or environment variables.")

        if self.debug_messages_path:
            append_debug_record(self.debug_messages_path, build_debug_record(self.settings, messages))

        from openai import OpenAI

        client = OpenAI(api_key=self.settings.api_key, base_url=self.settings.base_url)
        response = client.chat.completions.create(
            model=self.settings.model,
            messages=messages,
            response_format={"type": "json_object"},
            max_tokens=self.settings.max_tokens,
            temperature=self.settings.temperature,
        )
        content = response.choices[0].message.content or ""
        return parse_json_object(content)


def parse_json_object(text: str) -> dict[str, Any]:
    cleaned = text.strip()
    if not cleaned:
        raise ValueError("DeepSeek response is empty.")
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
        raise ValueError("DeepSeek response must be a JSON object.")
    return parsed
