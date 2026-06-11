"""Auth failures must surface as actionable Chinese messages, not raw stack text.

Field bug: a refused Volc ASR key showed the raw
``server rejected WebSocket connection: HTTP 401`` to the user. ASR and LLM auth
errors (401/403) are now mapped to "请检查 Key…" messages; LLM wording is generic
("LLM", not a fixed vendor) since the engine is pluggable.
"""
from __future__ import annotations

from sales_retro_agent.asr_volc import friendly_ws_error, ws_status_code
from sales_retro_agent.deepseek_client import friendly_llm_error


class _Resp:
    def __init__(self, status_code: int) -> None:
        self.status_code = status_code


class _NewStyleReject(Exception):
    """websockets>=12 InvalidStatus shape: .response.status_code."""

    def __init__(self, status_code: int) -> None:
        super().__init__(f"HTTP {status_code}")
        self.response = _Resp(status_code)


class _OldStyleReject(Exception):
    """older websockets InvalidStatusCode shape: .status_code."""

    def __init__(self, status_code: int) -> None:
        super().__init__(f"HTTP {status_code}")
        self.status_code = status_code


def test_ws_status_code_reads_both_shapes() -> None:
    assert ws_status_code(_NewStyleReject(401)) == 401
    assert ws_status_code(_OldStyleReject(403)) == 403
    assert ws_status_code(Exception("no status")) is None


def test_friendly_ws_error_maps_auth_to_chinese() -> None:
    for exc in (_NewStyleReject(401), _OldStyleReject(403)):
        mapped = friendly_ws_error(exc)
        assert isinstance(mapped, RuntimeError)
        assert "火山 ASR 鉴权失败" in str(mapped)
        assert "Resource ID" in str(mapped)


def test_friendly_ws_error_passes_through_non_auth() -> None:
    # A non-auth handshake/other error is returned unchanged (not masked).
    original = _NewStyleReject(500)
    assert friendly_ws_error(original) is original


def test_friendly_llm_error_maps_auth_and_is_vendor_neutral() -> None:
    for status in (401, 403):
        exc = Exception("unauthorized")
        exc.status_code = status  # openai APIStatusError exposes .status_code
        mapped = friendly_llm_error(exc)
        assert isinstance(mapped, RuntimeError)
        assert "LLM 鉴权失败" in str(mapped)
        # Wording must stay generic, not name a fixed vendor.
        assert "DeepSeek" not in str(mapped)


def test_friendly_llm_error_passes_through_non_auth() -> None:
    exc = Exception("rate limited")
    exc.status_code = 429
    assert friendly_llm_error(exc) is exc
    assert friendly_llm_error(Exception("no status")) is not None
