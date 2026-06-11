from __future__ import annotations

import asyncio
import json
import uuid
from collections.abc import AsyncIterator
from dataclasses import dataclass
from typing import Any, Callable

import websockets
from websockets.exceptions import InvalidHandshake

from .asr_types import TranscriptEvent
from .config import VolcAsrSettings
from .volc_protocol import (
    SERVER_ERROR_RESPONSE,
    build_audio_request,
    build_full_client_request,
    parse_server_message,
)


def ws_status_code(exc: Exception) -> int | None:
    """HTTP status from a websockets handshake rejection, across library versions
    (>=12: ``InvalidStatus(.response.status_code)``; older: ``InvalidStatusCode(.status_code)``)."""
    response = getattr(exc, "response", None)
    status = getattr(response, "status_code", None)
    if status is None:
        status = getattr(exc, "status_code", None)
    return status


def friendly_ws_error(exc: Exception) -> Exception:
    """Turn a Volc ASR handshake rejection into an actionable message. A 401/403
    means the credentials were refused — the most common real-world failure."""
    status = ws_status_code(exc)
    if status in (401, 403):
        return RuntimeError(
            f"火山 ASR 鉴权失败（HTTP {status}）：请检查火山 ASR 的 API Key 与 Resource ID 是否正确、"
            "对应的语音识别资源是否仍然有效。"
        )
    return exc


@dataclass(slots=True)
class VolcAsrEngine:
    settings: VolcAsrSettings
    audio_chunks: AsyncIterator[bytes]
    print_json: bool = False
    final_wait_seconds: float | None = 10.0
    debug_callback: Callable[[str, dict[str, Any]], None] | None = None

    def _debug(self, event_type: str, data: dict[str, Any] | None = None) -> None:
        if self.debug_callback:
            self.debug_callback(event_type, data or {})

    async def transcribe(self) -> AsyncIterator[TranscriptEvent]:
        if not self.settings.api_key and not (self.settings.app_key and self.settings.access_key):
            raise RuntimeError("Set VOLC_ASR_API_KEY, or VOLC_ASR_APP_KEY + VOLC_ASR_ACCESS_KEY.")

        queue: asyncio.Queue[TranscriptEvent | Exception | None] = asyncio.Queue()
        task = asyncio.create_task(self._run(queue), name="volc-asr-engine")
        try:
            while True:
                item = await queue.get()
                if item is None:
                    break
                if isinstance(item, Exception):
                    raise item
                yield item
        finally:
            if not task.done():
                task.cancel()
            await asyncio.gather(task, return_exceptions=True)

    async def _run(self, queue: asyncio.Queue[TranscriptEvent | Exception | None]) -> None:
        try:
            await self._stream(queue)
        except InvalidHandshake as exc:  # connection refused at handshake (e.g. 401)
            await queue.put(friendly_ws_error(exc))
        except Exception as exc:  # noqa: BLE001 - forwarded to async iterator consumer
            await queue.put(exc)
        finally:
            await queue.put(None)

    async def _stream(self, queue: asyncio.Queue[TranscriptEvent | Exception | None]) -> None:
        headers = {
            "X-Api-Resource-Id": self.settings.resource_id,
            "X-Api-Connect-Id": str(uuid.uuid4()),
        }
        if self.settings.api_key:
            headers["X-Api-Key"] = self.settings.api_key
        else:
            headers["X-Api-App-Key"] = self.settings.app_key
            headers["X-Api-Access-Key"] = self.settings.access_key

        self._debug("asr_ws_connecting", {"url": self.settings.ws_url, "resourceId": self.settings.resource_id})
        async with websockets.connect(
            self.settings.ws_url,
            additional_headers=headers,
            ping_interval=None,
            max_size=16 * 1024 * 1024,
            proxy=None,
            open_timeout=15,
        ) as ws:
            self._debug("asr_ws_connected")
            await ws.send(build_full_client_request(build_init_payload(self.settings)))
            self._debug("asr_init_sent")
            stop_event = asyncio.Event()

            async def receiver() -> None:
                last_text = ""
                while not stop_event.is_set():
                    try:
                        data = await ws.recv()
                    except websockets.ConnectionClosed:
                        stop_event.set()
                        break
                    if isinstance(data, str):
                        if self.print_json:
                            print(data, flush=True)
                        self._debug("asr_receiver_text_message", {"chars": len(data)})
                        continue

                    message = parse_server_message(data)
                    if self.print_json:
                        print(json.dumps(message.payload, ensure_ascii=False), flush=True)
                    if message.message_type == SERVER_ERROR_RESPONSE:
                        self._debug(
                            "asr_server_error",
                            {"errorCode": message.error_code, "payload": message.payload},
                        )
                        raise RuntimeError(f"ASR service error {message.error_code}: {message.payload}")

                    text = extract_text(message.payload)
                    if text and text != last_text:
                        await queue.put(TranscriptEvent(text=text, is_final=is_final_payload(message.payload)))
                        last_text = text

            async def sender() -> None:
                sequence = 1
                try:
                    async for chunk in self.audio_chunks:
                        if stop_event.is_set():
                            break
                        if sequence == 1:
                            self._debug("asr_audio_first_chunk", {"bytes": len(chunk)})
                        elif sequence % 100 == 0:
                            self._debug("asr_audio_chunks_sent", {"chunks": sequence, "lastBytes": len(chunk)})
                        await ws.send(build_audio_request(sequence, chunk))
                        sequence += 1
                    if not stop_event.is_set():
                        await ws.send(build_audio_request(sequence, b"", final=True))
                        self._debug("asr_audio_final_chunk_sent", {"chunks": sequence})
                except websockets.ConnectionClosed:
                    self._debug("asr_sender_connection_closed")
                    stop_event.set()

            receiver_task = asyncio.create_task(receiver(), name="volc-asr-receiver")
            sender_task = asyncio.create_task(sender(), name="volc-asr-sender")
            tasks = {receiver_task, sender_task}
            try:
                if self.final_wait_seconds is None:
                    done, pending = await asyncio.wait(tasks, return_when=asyncio.FIRST_EXCEPTION)
                    for task in done:
                        task.result()
                    await asyncio.gather(*pending)
                else:
                    await sender_task
                    try:
                        await asyncio.wait_for(receiver_task, timeout=self.final_wait_seconds)
                    except asyncio.TimeoutError:
                        pass
            finally:
                stop_event.set()
                await ws.close()
                for task in tasks:
                    if not task.done():
                        task.cancel()
                await asyncio.gather(*tasks, return_exceptions=True)


def build_init_payload(settings: VolcAsrSettings) -> dict[str, Any]:
    return {
        "user": {"uid": f"{settings.uid}_{uuid.uuid4().hex[:8]}"},
        "audio": {
            "format": "pcm",
            "codec": "raw",
            "rate": 16000,
            "bits": 16,
            "channel": 1,
            "language": settings.language,
        },
        "request": {
            "model_name": "bigmodel",
            "enable_itn": True,
            "enable_punc": True,
            "show_utterances": True,
        },
    }


def extract_text(payload: Any) -> str | None:
    if not isinstance(payload, dict):
        return None
    result = payload.get("result")
    if isinstance(result, dict) and isinstance(result.get("text"), str):
        return result["text"]
    if isinstance(payload.get("text"), str):
        return payload["text"]
    return None


def is_final_payload(payload: Any) -> bool:
    if not isinstance(payload, dict):
        return False
    result = payload.get("result")
    if isinstance(result, dict):
        return bool(result.get("is_final") or result.get("final"))
    return bool(payload.get("is_final") or payload.get("final"))
