"""Regression tests for the upload audio-decode path.

Covers the two fixes shipped together:

1. ASR error 45000081 ("waiting next packet timeout"): the file is now decoded
   to PCM up front (off the event loop) so no ASR session is open while
   decoding. Upload chunks are then paced at the audio's real-time rate
   (``realtime=True``) — full-speed blasting overruns the streaming engine and
   truncates the transcript (113 min -> 608 chars in the field).
2. The soundfile -> PyAV switch, so browser/phone formats (webm/opus, m4a/aac,
   mp3) decode without a system ffmpeg install.
"""
from __future__ import annotations

import asyncio
import math
import wave
from pathlib import Path

import numpy as np
import pytest

from sales_retro_agent.audio_sources import (
    TARGET_RATE,
    decode_file_to_pcm16,
    iter_pcm_chunks,
)

av = pytest.importorskip("av")


def _write_sine(path: Path, *, codec: str, container_rate: int, seconds: float) -> None:
    """Encode a mono sine tone to ``path`` using PyAV (so no test fixtures)."""
    t = np.linspace(0, seconds, int(container_rate * seconds), endpoint=False)
    tone = (np.sin(2 * math.pi * 440 * t) * 0.3 * 32767).astype("<i2")

    with av.open(str(path), mode="w") as container:
        stream = container.add_stream(codec, rate=container_rate)
        stream.layout = "mono"
        frame = av.AudioFrame.from_ndarray(tone.reshape(1, -1), format="s16", layout="mono")
        frame.rate = container_rate
        for packet in stream.encode(frame):
            container.mux(packet)
        for packet in stream.encode(None):
            container.mux(packet)


def test_decode_wav_resamples_to_16k_mono(tmp_path: Path) -> None:
    src = tmp_path / "tone.wav"
    # A plain 44.1 kHz wav via the stdlib, decoded + resampled by PyAV.
    samples = (np.sin(2 * math.pi * 440 * np.linspace(0, 1.0, 44100)) * 0.3 * 32767).astype("<i2")
    with wave.open(str(src), "wb") as wav:
        wav.setnchannels(1)
        wav.setsampwidth(2)
        wav.setframerate(44100)
        wav.writeframes(samples.tobytes())

    pcm = decode_file_to_pcm16(src)
    # ~1 s at 16 kHz, 2 bytes/sample. Allow slack for resampler edge frames.
    expected = TARGET_RATE * 2
    assert abs(len(pcm) - expected) < expected * 0.1
    assert len(pcm) % 2 == 0


@pytest.mark.parametrize(
    ("codec", "suffix", "rate"),
    [
        ("libmp3lame", ".mp3", 44100),
        ("aac", ".m4a", 44100),
        ("libopus", ".webm", 48000),
    ],
)
def test_decode_compressed_formats(tmp_path: Path, codec: str, suffix: str, rate: int) -> None:
    # soundfile could not read any of these; PyAV (bundled ffmpeg) can.
    try:
        src = tmp_path / f"tone{suffix}"
        _write_sine(src, codec=codec, container_rate=rate, seconds=1.0)
    except Exception:  # noqa: BLE001 - encoder may be absent in a minimal ffmpeg build
        pytest.skip(f"PyAV build lacks the {codec} encoder; decode path still uses bundled ffmpeg")

    pcm = decode_file_to_pcm16(src)
    assert len(pcm) > 0
    assert len(pcm) % 2 == 0


def test_decode_rejects_file_without_audio(tmp_path: Path) -> None:
    bad = tmp_path / "not_audio.bin"
    bad.write_bytes(b"\x00\x01\x02not a media file")
    with pytest.raises(Exception):  # noqa: B017 - PyAV raises its own error type
        decode_file_to_pcm16(bad)


def test_iter_pcm_chunks_slices_and_streams() -> None:
    pcm = b"\x01\x02" * (TARGET_RATE)  # 1 s of fake s16 mono

    async def collect() -> list[bytes]:
        return [chunk async for chunk in iter_pcm_chunks(pcm, chunk_ms=200, realtime=False)]

    chunks = asyncio.run(collect())
    # 1 s / 200 ms = 5 chunks; each 200 ms = 3200 samples * 2 bytes.
    assert len(chunks) == 5
    assert all(len(c) == int(TARGET_RATE * 0.2) * 2 for c in chunks)
    assert b"".join(chunks) == pcm


def test_iter_pcm_chunks_realtime_false_does_not_sleep() -> None:
    pcm = b"\x00\x00" * (TARGET_RATE * 3)  # 3 s of audio

    async def timed() -> float:
        loop = asyncio.get_event_loop()
        start = loop.time()
        async for _ in iter_pcm_chunks(pcm, chunk_ms=200, realtime=False):
            pass
        return loop.time() - start

    # realtime=False must not wall-clock pace; 3 s of audio drains near-instantly.
    assert asyncio.run(timed()) < 0.5


def test_iter_pcm_chunks_realtime_true_paces_to_audio_clock() -> None:
    # Guards the upload fix: realtime=True must pace at the audio's wall-clock
    # rate. Full-speed blasting overran Volc SAUC and truncated the transcript.
    pcm = b"\x00\x00" * (TARGET_RATE * 1)  # 1 s of audio

    async def timed() -> float:
        loop = asyncio.get_event_loop()
        start = loop.time()
        async for _ in iter_pcm_chunks(pcm, chunk_ms=200, realtime=True):
            pass
        return loop.time() - start

    # 1 s of audio in 200 ms chunks => ~5 * 0.2 s of sleeps. Allow scheduler slack
    # but require it is clearly paced (not drained instantly like realtime=False).
    assert asyncio.run(timed()) >= 0.8
