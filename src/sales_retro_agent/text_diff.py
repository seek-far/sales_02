from __future__ import annotations

from dataclasses import dataclass


@dataclass
class TranscriptCursor:
    """Track incremental transcript text from a cumulative source."""

    seen_text: str = ""

    def diff(self, latest_text: str) -> str:
        if not latest_text:
            return ""
        if not self.seen_text:
            self.seen_text = latest_text
            return latest_text
        if latest_text.startswith(self.seen_text):
            new_text = latest_text[len(self.seen_text) :]
            self.seen_text = latest_text
            return new_text

        overlap = longest_suffix_prefix_overlap(self.seen_text, latest_text)
        new_text = latest_text[overlap:]
        self.seen_text = latest_text
        return new_text


def longest_suffix_prefix_overlap(previous: str, latest: str) -> int:
    max_len = min(len(previous), len(latest))
    for length in range(max_len, 0, -1):
        if previous[-length:] == latest[:length]:
            return length
    return 0
