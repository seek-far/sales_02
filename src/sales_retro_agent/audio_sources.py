from __future__ import annotations

import asyncio
import math
from collections.abc import AsyncIterator
from pathlib import Path

import numpy as np
import soundfile as sf


TARGET_RATE = 16_000
TARGET_CHANNELS = 1


def _import_sounddevice():  # noqa: ANN202 - returns the sounddevice module
    """Import sounddevice lazily.

    Only the microphone-capture paths (``list_input_devices`` /
    ``iter_microphone_pcm_chunks``) need PortAudio. The thin web backend uses
    only ``iter_file_pcm_chunks`` (numpy + soundfile), so importing this module
    must not require the native PortAudio library.
    """
    import sounddevice as sd

    return sd


def list_input_devices() -> list[tuple[int, str]]:
    sd = _import_sounddevice()
    devices = sd.query_devices()
    result: list[tuple[int, str]] = []
    for index, device in enumerate(devices):
        if int(device.get("max_input_channels", 0)) > 0:
            result.append((index, str(device.get("name", ""))))
    return result


def _to_mono_float32(samples: np.ndarray) -> np.ndarray:
    samples = np.asarray(samples)
    if samples.ndim == 1:
        return samples.astype(np.float32, copy=False)
    return samples.mean(axis=1, dtype=np.float32)


def _resample_linear(samples: np.ndarray, source_rate: int, target_rate: int) -> np.ndarray:
    if source_rate == target_rate or len(samples) == 0:
        return samples.astype(np.float32, copy=False)

    duration = len(samples) / float(source_rate)
    target_len = max(1, int(round(duration * target_rate)))
    source_positions = np.linspace(0, len(samples) - 1, num=len(samples), dtype=np.float64)
    target_positions = np.linspace(0, len(samples) - 1, num=target_len, dtype=np.float64)
    return np.interp(target_positions, source_positions, samples).astype(np.float32)


def float32_to_pcm16(samples: np.ndarray) -> bytes:
    samples = np.clip(samples, -1.0, 1.0)
    return (samples * 32767.0).astype("<i2").tobytes()


async def iter_file_pcm_chunks(path: str | Path, *, chunk_ms: int, realtime: bool = True) -> AsyncIterator[bytes]:
    audio, sample_rate = sf.read(str(path), dtype="float32", always_2d=False)
    mono = _to_mono_float32(audio)
    mono = _resample_linear(mono, int(sample_rate), TARGET_RATE)

    chunk_samples = int(TARGET_RATE * chunk_ms / 1000)
    sleep_seconds = chunk_ms / 1000
    total_chunks = math.ceil(len(mono) / chunk_samples) if len(mono) else 0

    for chunk_index in range(total_chunks):
        start = chunk_index * chunk_samples
        end = start + chunk_samples
        payload = float32_to_pcm16(mono[start:end])
        if payload:
            yield payload
        if realtime:
            await asyncio.sleep(sleep_seconds)


async def iter_microphone_pcm_chunks(*, chunk_ms: int, device: int | None = None) -> AsyncIterator[bytes]:
    sd = _import_sounddevice()
    chunk_samples = int(TARGET_RATE * chunk_ms / 1000)
    loop = asyncio.get_running_loop()
    audio_queue: asyncio.Queue[bytes] = asyncio.Queue(maxsize=20)

    def put_audio(payload: bytes) -> None:
        try:
            audio_queue.put_nowait(payload)
        except asyncio.QueueFull:
            pass

    def callback(indata: np.ndarray, _frames: int, _time, status) -> None:  # noqa: ANN001
        if status:
            print(f"[audio] {status}", flush=True)
        loop.call_soon_threadsafe(put_audio, float32_to_pcm16(indata[:, 0]))

    with sd.InputStream(
        samplerate=TARGET_RATE,
        channels=TARGET_CHANNELS,
        dtype="float32",
        blocksize=chunk_samples,
        device=device,
        callback=callback,
    ):
        while True:
            yield await audio_queue.get()
