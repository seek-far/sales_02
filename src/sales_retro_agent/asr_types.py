from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol, AsyncIterator


@dataclass(frozen=True)
class TranscriptEvent:
    text: str
    is_final: bool = False
    start_ms: int | None = None
    end_ms: int | None = None
    speaker: str | None = None


class AsrEngine(Protocol):
    async def transcribe(self) -> AsyncIterator[TranscriptEvent]:
        ...
