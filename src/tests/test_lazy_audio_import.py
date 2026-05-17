"""Regression tests for §6 step 1: sounddevice lazy-import decoupling.

The thin web backend (`sales_retro_agent.web`) must import and run without the
native PortAudio library, because only the CLI microphone path needs it. These
tests fail if anyone re-adds a top-level `import sounddevice`.
"""
from __future__ import annotations

import importlib
import sys


def _purge(*prefixes: str) -> None:
    for name in list(sys.modules):
        if any(name == p or name.startswith(p + ".") for p in prefixes):
            del sys.modules[name]


def test_audio_sources_import_does_not_pull_sounddevice() -> None:
    _purge("sales_retro_agent", "sounddevice")
    importlib.import_module("sales_retro_agent.audio_sources")
    assert "sounddevice" not in sys.modules, (
        "importing audio_sources must not import sounddevice (PortAudio)"
    )


def test_web_backend_import_does_not_pull_sounddevice() -> None:
    _purge("sales_retro_agent", "sounddevice")
    web = importlib.import_module("sales_retro_agent.web")
    assert "sounddevice" not in sys.modules, (
        "the thin web backend must not require PortAudio at import time"
    )
    # Sanity: the file-decode path the backend actually uses is still present.
    from sales_retro_agent.audio_sources import iter_file_pcm_chunks

    assert callable(iter_file_pcm_chunks)
    assert web.STATIC_DIR.exists()


def test_microphone_path_still_uses_sounddevice_lazily() -> None:
    audio_sources = importlib.import_module("sales_retro_agent.audio_sources")
    # The lazy importer exists and the mic functions are still exported.
    assert hasattr(audio_sources, "_import_sounddevice")
    assert hasattr(audio_sources, "iter_microphone_pcm_chunks")
    assert hasattr(audio_sources, "list_input_devices")
