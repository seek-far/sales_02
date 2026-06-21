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
    SERVER_FULL_RESPONSE,
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
    # Emit raw server payloads as `asr_raw_payload` debug events so a diagnostics
    # bundle carries real frames for field verification (utterances[].definite,
    # .additions.speaker, and the partial→definite finalization transition where the
    # dedup bug lives). Two buckets: a few early (partial) frames + the first frames
    # that actually contain a finalized/multi utterance. Capped so the log stays small.
    raw_frame_capture_early: int = 6
    raw_frame_capture_final: int = 14
    # Label speaker turns ([说话人N]) from utterances[].additions.speaker_id.
    enable_speaker_labels: bool = True

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
            init_payload = build_init_payload(self.settings)
            await ws.send(build_full_client_request(init_payload))
            # Log the request params actually sent so a diagnostics bundle shows
            # whether speaker-separation flags (ssd_version / enable_speaker_info /
            # enable_nonstream) really went out — the missing-ssd_version bug was
            # invisible before because nothing recorded the init request.
            self._debug(
                "asr_init_sent",
                {"request": init_payload.get("request"), "language": init_payload.get("audio", {}).get("language")},
            )
            stop_event = asyncio.Event()

            async def receiver() -> None:
                last_text = ""
                raw_early = 0
                raw_final = 0
                accumulator = UtteranceAccumulator(speaker_labels=self.enable_speaker_labels)
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

                    if message.message_type == SERVER_FULL_RESPONSE:
                        utts = []
                        if isinstance(message.payload, dict):
                            utts = (message.payload.get("result") or {}).get("utterances") or []
                        has_final = bool(utts) and (len(utts) > 1 or any(u.get("definite") for u in utts))
                        if raw_early < self.raw_frame_capture_early:
                            raw_early += 1
                            self._debug("asr_raw_payload", {"phase": "early", "frame": raw_early, "payload": message.payload})
                        elif has_final and raw_final < self.raw_frame_capture_final:
                            raw_final += 1
                            self._debug("asr_raw_payload", {"phase": "final", "frame": raw_final, "payload": message.payload})

                    utterances = utterances_of(message.payload)
                    if utterances is not None:
                        # New-style payload: emit only committed (definite) text so the
                        # downstream cursor sees an append-only source — no ITN-rewrite
                        # duplication. Provisional text is held back until it finalizes.
                        text = accumulator.update(utterances)
                        speaker = accumulator.last_speaker
                    else:
                        # Legacy/variant payload without utterances: keep old behaviour.
                        text = extract_text(message.payload)
                        speaker = None
                    if text and text != last_text:
                        await queue.put(
                            TranscriptEvent(
                                text=text,
                                is_final=is_final_payload(message.payload),
                                speaker=speaker,
                            )
                        )
                        last_text = text

                # Stream ended: flush a trailing provisional utterance that never
                # finalized so the tail isn't lost from the transcript.
                tail = accumulator.flush_partial()
                if tail and tail != last_text:
                    await queue.put(TranscriptEvent(text=tail, is_final=True, speaker=accumulator.last_speaker))

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
            # Speaker (voiceprint) clustering: labels land in
            # utterances[].additions.speaker_id ("0"/"1"/...) on finalized utterances.
            # Per the streaming doc this needs ssd_version="200" AND language unset/zh-CN
            # to actually engage — without ssd_version it silently returns a single
            # speaker ("0"). (If bigmodel_async turns out to be the 双向流式优化接口,
            # it may additionally need enable_nonstream=true.)
            "enable_speaker_info": True,
            "ssd_version": "200",
            # bigmodel_async appears to be the 双向流式优化接口, for which the doc
            # says speaker separation additionally requires enable_nonstream=true.
            "enable_nonstream": True,
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


def utterances_of(payload: Any) -> list[dict] | None:
    """Return result.utterances when present (new-style payload), else None so the
    caller falls back to the flattened result.text path."""
    if not isinstance(payload, dict):
        return None
    result = payload.get("result")
    if not isinstance(result, dict):
        return None
    utts = result.get("utterances")
    return utts if isinstance(utts, list) else None


class UtteranceAccumulator:
    """Build a clean, append-only transcript from Volc streaming utterances.

    Volc emits ``result.utterances[]`` where each utterance carries ``definite``
    (false=provisional, true=finalized) and, once finalized, ITN/punctuation is
    applied (e.g. "90"->"九十"). The flattened ``result.text`` therefore gets
    *rewritten* at finalization, which broke ``TranscriptCursor`` (it re-appended
    the whole restated sentence -> duplicate lines).

    We instead commit only ``definite`` utterances, keyed by their stable
    (start_time, end_time) so a committed sentence is never re-added even if the
    utterance list slides. The committed text is strictly append-only, so any
    cursor diff downstream is duplication-free by construction. Speaker turns
    (``additions.speaker_id``, present on finalized utterances) are labelled as
    ``[说话人N]`` when the speaker changes.
    """

    def __init__(self, speaker_labels: bool = True) -> None:
        self.speaker_labels = speaker_labels
        self._seen: set[tuple[Any, Any]] = set()
        self._segments: list[tuple[str | None, str]] = []  # (speaker_id, text)
        self._partial: tuple[str | None, str] | None = None

    @staticmethod
    def _segment(utt: dict) -> tuple[str | None, str]:
        speaker = (utt.get("additions") or {}).get("speaker_id")
        return speaker, (utt.get("text") or "").strip()

    def update(self, utterances: list[dict]) -> str:
        """Commit any newly-finalized utterances; return the full committed text."""
        for utt in utterances:
            if not isinstance(utt, dict) or not utt.get("definite"):
                continue
            key = (utt.get("start_time"), utt.get("end_time"))
            if key in self._seen:
                continue
            self._seen.add(key)
            self._segments.append(self._segment(utt))
        # Remember the trailing provisional text so it can be flushed at stream end
        # (otherwise an utterance that never finalizes — e.g. on early termination —
        # would be dropped from the transcript).
        self._partial = None
        for utt in utterances:
            if isinstance(utt, dict) and not utt.get("definite") and (utt.get("text") or "").strip():
                self._partial = self._segment(utt)
        return self.render()

    def flush_partial(self) -> str:
        """Promote the last provisional utterance to committed (used at stream end)."""
        if self._partial is not None:
            self._segments.append(self._partial)
            self._partial = None
        return self.render()

    def render(self) -> str:
        lines: list[str] = []
        last_speaker: Any = object()
        for speaker, text in self._segments:
            if not text:
                continue
            if self.speaker_labels and speaker is not None and speaker != last_speaker:
                lines.append(f"[说话人{speaker}] {text}")
            else:
                lines.append(text)
            last_speaker = speaker
        return "\n".join(lines)

    @property
    def last_speaker(self) -> str | None:
        return self._segments[-1][0] if self._segments else None
