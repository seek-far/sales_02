from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator
from pathlib import Path
from typing import Any

import numpy as np


TARGET_RATE = 16_000
TARGET_CHANNELS = 1


def _import_sounddevice():  # noqa: ANN202 - returns the sounddevice module
    """Import sounddevice lazily.

    Only the microphone-capture paths (``list_input_devices`` /
    ``iter_microphone_pcm_chunks``) need PortAudio. The thin web backend uses
    only the file-decode path (numpy + PyAV), so importing this module must not
    require the native PortAudio library. Guarded by
    ``tests/test_lazy_audio_import.py``.
    """
    import sounddevice as sd

    return sd


def _import_av():  # noqa: ANN202 - returns the av module
    """Import PyAV lazily.

    PyAV's wheels bundle the ffmpeg libraries, so the file-decode path can read
    any container/codec the user uploads (wav/flac/ogg/mp3/m4a/webm/...) without
    requiring a system ffmpeg install. Importing it lazily keeps module import
    cheap for the mic-only / test paths that never decode a file.
    """
    import av

    return av


def list_input_devices() -> list[tuple[int, str]]:
    sd = _import_sounddevice()
    devices = sd.query_devices()
    result: list[tuple[int, str]] = []
    for index, device in enumerate(devices):
        if int(device.get("max_input_channels", 0)) > 0:
            result.append((index, str(device.get("name", ""))))
    return result


def float32_to_pcm16(samples: np.ndarray) -> bytes:
    samples = np.clip(samples, -1.0, 1.0)
    return (samples * 32767.0).astype("<i2").tobytes()


def _resample_frames(resampler: Any, frame: Any) -> list[Any]:  # noqa: ANN401
    """Normalise ``AudioResampler.resample`` across PyAV versions.

    PyAV >= 9 returns a list of frames; older versions returned a single frame
    or ``None``. ``frame=None`` flushes any buffered samples.
    """
    out = resampler.resample(frame)
    if out is None:
        return []
    if isinstance(out, list):
        return out
    return [out]


def decode_file_to_pcm16(path: str | Path) -> bytes:
    """Decode any audio file to 16 kHz mono signed-16-bit little-endian PCM.

    Synchronous and CPU-bound — callers on the event loop must run it via
    ``asyncio.to_thread`` (and decode BEFORE opening the ASR WebSocket, so the
    first audio packet ships immediately and the server's 8 s
    ``waiting next packet`` deadline is never at risk).
    """
    av = _import_av()
    resampler = av.AudioResampler(format="s16", layout="mono", rate=TARGET_RATE)
    buffer = bytearray()
    with av.open(str(path)) as container:
        if not container.streams.audio:
            raise ValueError("上传的文件里没有音频流。")
        stream = container.streams.audio[0]
        for frame in container.decode(stream):
            for resampled in _resample_frames(resampler, frame):
                buffer += resampled.to_ndarray().astype("<i2").tobytes()
        for resampled in _resample_frames(resampler, None):
            buffer += resampled.to_ndarray().astype("<i2").tobytes()
    return bytes(buffer)


async def iter_pcm_chunks(
    pcm: bytes, *, chunk_ms: int, realtime: bool = False
) -> AsyncIterator[bytes]:
    """Yield already-decoded 16 kHz mono PCM in ``chunk_ms``-sized slices.

    Pass ``realtime=True`` to pace packets at the audio's wall-clock rate — the
    upload path needs this, because Volc SAUC is a streaming engine that overruns
    its buffer (and drops most of the transcript) if the whole file is sent at
    once. ``realtime=False`` drains instantly and is only for tests / non-stream
    consumers.
    """
    bytes_per_chunk = int(TARGET_RATE * chunk_ms / 1000) * 2  # 2 bytes/sample (s16)
    if bytes_per_chunk <= 0:
        return
    sleep_seconds = chunk_ms / 1000
    for start in range(0, len(pcm), bytes_per_chunk):
        payload = pcm[start : start + bytes_per_chunk]
        if payload:
            yield payload
        if realtime:
            await asyncio.sleep(sleep_seconds)


async def iter_file_pcm_chunks(
    path: str | Path, *, chunk_ms: int, realtime: bool = True
) -> AsyncIterator[bytes]:
    """Decode ``path`` off the event loop, then stream it as PCM chunks.

    The decode runs in a worker thread so it never blocks the loop. For the
    upload path prefer decoding up front (``decode_file_to_pcm16`` +
    ``iter_pcm_chunks``) so no ASR session is open while decoding.
    """
    pcm = await asyncio.to_thread(decode_file_to_pcm16, path)
    async for chunk in iter_pcm_chunks(pcm, chunk_ms=chunk_ms, realtime=realtime):
        yield chunk


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
