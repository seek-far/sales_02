from __future__ import annotations

import json
from dataclasses import asdict, dataclass
from datetime import datetime
from pathlib import Path
from typing import Any, Iterable

from .config import Settings


@dataclass(frozen=True)
class LlmDebugRecord:
    timestamp: str
    provider: str
    model: str
    base_url: str
    max_tokens: int
    temperature: float
    messages: list[dict[str, str]]


def build_debug_record(settings: Settings, messages: list[dict[str, str]]) -> LlmDebugRecord:
    return LlmDebugRecord(
        timestamp=datetime.now().isoformat(timespec="seconds"),
        provider="deepseek",
        model=settings.model,
        base_url=settings.base_url,
        max_tokens=settings.max_tokens,
        temperature=settings.temperature,
        messages=messages,
    )


def append_debug_record(path: Path, record: LlmDebugRecord) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as file:
        file.write(json.dumps(asdict(record), ensure_ascii=False) + "\n")


def iter_debug_records(path: Path) -> Iterable[LlmDebugRecord]:
    with path.open("r", encoding="utf-8") as file:
        for line_no, line in enumerate(file, start=1):
            stripped = line.strip()
            if not stripped:
                continue
            payload = json.loads(stripped)
            yield parse_debug_record(payload, line_no=line_no)


def parse_debug_record(payload: dict[str, Any], *, line_no: int = 0) -> LlmDebugRecord:
    messages = payload.get("messages")
    if not isinstance(messages, list):
        raise ValueError(f"Debug record line {line_no} must contain messages list.")
    for message in messages:
        if not isinstance(message, dict) or not isinstance(message.get("role"), str) or not isinstance(
            message.get("content"), str
        ):
            raise ValueError(f"Debug record line {line_no} contains an invalid message.")

    return LlmDebugRecord(
        timestamp=str(payload.get("timestamp", "")),
        provider=str(payload.get("provider", "deepseek")),
        model=str(payload.get("model", "")),
        base_url=str(payload.get("base_url", "")),
        max_tokens=int(payload.get("max_tokens", 12000)),
        temperature=float(payload.get("temperature", 0)),
        messages=messages,
    )


def settings_for_replay(base: Settings, record: LlmDebugRecord) -> Settings:
    return Settings(
        api_key=base.api_key,
        base_url=record.base_url or base.base_url,
        model=record.model or base.model,
        max_chunk_chars=base.max_chunk_chars,
        max_tokens=record.max_tokens,
        temperature=record.temperature,
    )
