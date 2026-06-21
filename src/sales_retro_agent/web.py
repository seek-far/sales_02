from __future__ import annotations

import argparse
import asyncio
import base64
import json
import mimetypes
import posixpath
import re
import secrets
import shutil
import threading
import time
import zipfile
from dataclasses import asdict
from datetime import datetime, timezone
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any
from urllib.parse import parse_qs, unquote, urlparse

from .asr_volc import VolcAsrEngine
from .audio_sources import TARGET_RATE, decode_file_to_pcm16, iter_pcm_chunks
from .coach_debug import build_coach_debug_record, snapshot_state
from .config import Settings, VolcAsrSettings, load_settings, load_volc_asr_settings
from .llm_coach import LLM_COACH_SYSTEM_PROMPT, LLMRealtimeCoach
from .realtime_coach import MeetingState, RealtimeSalesCoach
from .realtime_runner import chunk_text
from .text_diff import TranscriptCursor


ROOT = Path(__file__).resolve().parent
STATIC_DIR = ROOT / "web_static"
SESSION_ROOT = Path("outputs") / "web_sessions"
MAX_JSON_BYTES = 512 * 1024 * 1024

# Early-termination registry for in-flight coach-upload runs. A coach-upload
# request streams audio at real time on its own thread (ThreadingHTTPServer),
# so the "提前终止" button arrives on a *different* thread; we hand it a
# threading.Event the streaming loop polls between ASR events.
_CANCEL_EVENTS: dict[str, threading.Event] = {}
_CANCEL_LOCK = threading.Lock()


def register_coach_upload_cancel(session_id: Any) -> threading.Event:
    """Create (or reset) the cancel flag for a coach-upload run."""
    event = threading.Event()
    if session_id:
        with _CANCEL_LOCK:
            _CANCEL_EVENTS[str(session_id)] = event
    return event


def request_coach_upload_cancel(session_id: Any) -> bool:
    """Signal a running coach-upload to stop early. Returns True if one was live."""
    if not session_id:
        return False
    with _CANCEL_LOCK:
        event = _CANCEL_EVENTS.get(str(session_id))
    if event is None:
        return False
    event.set()
    return True


def clear_coach_upload_cancel(session_id: Any) -> None:
    if not session_id:
        return
    with _CANCEL_LOCK:
        _CANCEL_EVENTS.pop(str(session_id), None)


# Per-session single-flight guard: at most one coach-upload may run per session.
# A slow upload used to leave the run button live with no progress feedback, so
# users double-clicked and launched concurrent runs over the same session. Each
# run paces audio on its own clock and writes alerts into the same log, so their
# alerts interleaved and elapsedMinutes came out non-monotonic (e.g. 2,8,2,2,7).
# The frontend now disables the button on click; this is the server-side backstop
# in case a second request still arrives (extra tab, retry, etc.).
_RUNNING_SESSIONS: set[str] = set()
_RUNNING_LOCK = threading.Lock()


def try_begin_coach_upload(session_id: Any) -> bool:
    """Reserve a session for a coach-upload run. Returns False if one is already
    in flight for this session (caller must reject the duplicate request)."""
    if not session_id:
        return True
    with _RUNNING_LOCK:
        key = str(session_id)
        if key in _RUNNING_SESSIONS:
            return False
        _RUNNING_SESSIONS.add(key)
    return True


def end_coach_upload(session_id: Any) -> None:
    if not session_id:
        return
    with _RUNNING_LOCK:
        _RUNNING_SESSIONS.discard(str(session_id))


def is_coach_upload_running(session_id: Any) -> bool:
    """True if a coach-upload run is in flight for this session. Used to refuse
    clearing logs mid-run (that would rmtree the session dir being written)."""
    if not session_id:
        return False
    with _RUNNING_LOCK:
        return str(session_id) in _RUNNING_SESSIONS


class WebRequestHandler(BaseHTTPRequestHandler):
    server_version = "SalesRetroWeb/0.1"

    def do_GET(self) -> None:  # noqa: N802
        parsed = urlparse(self.path)
        if parsed.path == "/api/health":
            self.send_json({"ok": True, "time": now_iso()})
            return
        if parsed.path == "/api/default-config":
            self.send_json(default_config())
            return
        if parsed.path == "/api/logs/current":
            query = parse_qs(parsed.query)
            session_id = first(query.get("sessionId"))
            self.send_json(read_session_log(session_id))
            return
        if parsed.path == "/api/diagnostics":
            query = parse_qs(parsed.query)
            session_id = first(query.get("sessionId"))
            clear_after = str(first(query.get("clearAfter")) or "").lower() in ("1", "true", "yes")
            self.send_diagnostics(session_id, clear_after=clear_after)
            return
        self.send_static(parsed.path)

    def do_POST(self) -> None:  # noqa: N802
        parsed = urlparse(self.path)
        payload: dict[str, Any] = {}
        try:
            if parsed.path == "/api/sessions":
                payload = self.read_json()
                session = create_session(payload.get("config", {}))
                self.send_json(session)
                return
            if parsed.path == "/api/events":
                payload = self.read_json()
                append_event(payload.get("sessionId"), payload.get("type", "event"), payload.get("data", {}))
                self.send_json({"ok": True})
                return
            if parsed.path == "/api/audio-chunk":
                payload = self.read_json()
                result = save_audio_blob(payload, folder="chunks")
                append_event(
                    payload.get("sessionId"),
                    "audio_chunk_saved",
                    {
                        "path": result["path"],
                        "bytes": result["bytes"],
                        "mimeType": payload.get("mimeType", ""),
                        "chunkIndex": payload.get("chunkIndex"),
                    },
                )
                self.send_json(result)
                return
            if parsed.path == "/api/audio-upload":
                # Raw-binary upload by default: the browser POSTs the file bytes
                # directly (not base64-in-JSON), so there's no ~33% inflation and
                # no giant string to build/parse — large recordings save far
                # faster. Metadata rides in headers. A JSON body is still accepted
                # for backward compatibility.
                ctype = self.headers.get("Content-Type", "")
                if ctype.startswith("application/json"):
                    payload = self.read_json()
                    session_id = payload.get("sessionId")
                    mime_type = payload.get("mimeType", "")
                    result = save_audio_blob(payload, folder="uploads")
                else:
                    session_id = unquote(self.headers.get("X-Session-Id", ""))
                    file_name = unquote(self.headers.get("X-File-Name", "") or "upload.webm")
                    mime_type = unquote(self.headers.get("X-File-Type", ""))
                    result = save_audio_bytes(session_id, file_name, self.read_body(), folder="uploads")
                append_event(
                    session_id,
                    "audio_upload_saved",
                    {"path": result["path"], "bytes": result["bytes"], "mimeType": mime_type},
                )
                self.send_json(result)
                return
            if parsed.path == "/api/coach-transcript":
                payload = self.read_json()
                result = coach_transcript(payload)
                self.send_json(result)
                return
            if parsed.path == "/api/coach-upload":
                payload = self.read_json()
                session_id = payload.get("sessionId")
                if not try_begin_coach_upload(session_id):
                    append_event(session_id, "coach_upload_rejected", {"reason": "already_running"})
                    self.send_json(
                        {
                            "ok": False,
                            "error": "AlreadyRunning",
                            "message": "该会话已有转写任务在运行，请等待其结束或先点「提前终止」。",
                        },
                        status=HTTPStatus.CONFLICT,
                    )
                    return
                try:
                    result = asyncio.run(coach_uploaded_audio(payload))
                finally:
                    end_coach_upload(session_id)
                self.send_json(result)
                return
            if parsed.path == "/api/coach-upload/cancel":
                payload = self.read_json()
                cancelled = request_coach_upload_cancel(payload.get("sessionId"))
                if cancelled:
                    append_event(payload.get("sessionId"), "coach_upload_cancel_requested", {})
                self.send_json({"ok": True, "cancelled": cancelled})
                return
            if parsed.path == "/api/logs/clear":
                payload = self.read_json()
                session_id = payload.get("sessionId")
                if is_coach_upload_running(session_id):
                    self.send_json(
                        {
                            "ok": False,
                            "error": "Running",
                            "message": "转写运行中，请先「提前终止」再清除日志。",
                        },
                        status=HTTPStatus.CONFLICT,
                    )
                    return
                clear_logs(session_id)
                self.send_json({"ok": True})
                return
        except Exception as exc:  # noqa: BLE001 - HTTP boundary returns structured errors
            session_id = payload.get("sessionId") if isinstance(payload, dict) else None
            if session_id:
                append_event(
                    session_id,
                    "request_failed",
                    {"path": parsed.path, "error": type(exc).__name__, "message": str(exc)},
                )
            self.send_json(
                {"ok": False, "error": type(exc).__name__, "message": str(exc)},
                status=HTTPStatus.BAD_REQUEST,
            )
            return
        self.send_error(HTTPStatus.NOT_FOUND)

    def read_body(self) -> bytes:
        """Read the raw request body (used by the binary audio-upload path)."""
        length = int(self.headers.get("Content-Length", "0"))
        if length > MAX_JSON_BYTES:
            raise ValueError("Request body is too large.")
        return self.rfile.read(length) if length else b""

    def read_json(self) -> dict[str, Any]:
        length = int(self.headers.get("Content-Length", "0"))
        if length > MAX_JSON_BYTES:
            raise ValueError("Request body is too large.")
        raw = self.rfile.read(length)
        if not raw:
            return {}
        payload = json.loads(raw.decode("utf-8"))
        if not isinstance(payload, dict):
            raise ValueError("JSON body must be an object.")
        return payload

    def send_json(self, payload: Any, *, status: HTTPStatus = HTTPStatus.OK) -> None:
        body = json.dumps(payload, ensure_ascii=False, indent=2).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(body)

    def send_static(self, request_path: str) -> None:
        if request_path in {"", "/"}:
            # This deployment is the thin-backend LLM build; the root must land
            # on backend.html, not the rules-only pure-frontend index.html.
            request_path = "/backend.html"
        normalized = posixpath.normpath(request_path.lstrip("/"))
        if normalized.startswith(".."):
            self.send_error(HTTPStatus.FORBIDDEN)
            return
        path = STATIC_DIR / normalized
        if not path.exists() or not path.is_file():
            self.send_error(HTTPStatus.NOT_FOUND)
            return
        body = path.read_bytes()
        content_type = mimetypes.guess_type(str(path))[0] or "application/octet-stream"
        self.send_response(HTTPStatus.OK)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def send_diagnostics(self, session_id: str | None, clear_after: bool = False) -> None:
        session_dir = resolve_session_dir(session_id)
        if not session_dir.exists():
            raise ValueError("Session does not exist.")
        package_path = session_dir / "diagnostic_package.zip"
        with zipfile.ZipFile(package_path, "w", zipfile.ZIP_DEFLATED) as package:
            for path in session_dir.rglob("*"):
                if path.is_file() and path.name != package_path.name:
                    package.write(path, path.relative_to(session_dir))
        body = package_path.read_bytes()
        # Atomic export+clear: the zip is fully in memory now, so clearing the
        # session dir here cannot corrupt the download. Doing it inside this one
        # request (instead of a concurrent /api/logs/clear) removes the
        # rmtree-vs-zip race on ThreadingHTTPServer — that race is why the
        # browser-side fetch+Blob "await then clear" was needed, which itself
        # silently failed behind the HTTPS proxy. Now the client just navigates.
        if clear_after and session_id and not is_coach_upload_running(session_id):
            clear_logs(session_id)
        self.send_response(HTTPStatus.OK)
        self.send_header("Content-Type", "application/zip")
        self.send_header("Content-Disposition", f'attachment; filename="sales-retro-{session_dir.name}.zip"')
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, format: str, *args: Any) -> None:
        print(f"[web] {self.address_string()} - {format % args}", flush=True)


def create_session(config: dict[str, Any]) -> dict[str, Any]:
    session_id = datetime.now().strftime("%Y%m%d_%H%M%S_") + secrets.token_hex(4)
    session_dir = SESSION_ROOT / session_id
    session_dir.mkdir(parents=True, exist_ok=True)
    session = {
        "sessionId": session_id,
        "startedAt": now_iso(),
        "configSnapshot": sanitize_config(config),
    }
    (session_dir / "session.json").write_text(json.dumps(session, ensure_ascii=False, indent=2), encoding="utf-8")
    append_event(session_id, "session_started", session)
    return session


def default_config() -> dict[str, Any]:
    deepseek = load_settings()
    volc = load_volc_asr_settings()
    return {
        "prompt": LLM_COACH_SYSTEM_PROMPT,
        "uploadIntervalSeconds": 60,
        # LLM-only deployment: always default to the DeepSeek LLM coach. The
        # user supplies the key via the in-app dialog, not server env.
        "coachEngine": "llm",
        "deepseekApiKey": deepseek.api_key,
        "deepseekBaseUrl": deepseek.base_url,
        "deepseekModel": deepseek.model,
        "volcAsrApiKey": volc.api_key,
        "volcAsrResourceId": volc.resource_id,
        "volcAsrWsUrl": volc.ws_url,
        "volcAsrLanguage": volc.language,
        "meetingDurationMinutes": 90,
        "charsPerStep": 800,
    }


def append_event(session_id: str | None, event_type: str, data: Any) -> None:
    session_dir = resolve_session_dir(session_id)
    session_dir.mkdir(parents=True, exist_ok=True)
    record = {
        "timestamp": now_iso(),
        "type": str(event_type),
        "data": sanitize_config(data) if isinstance(data, dict) else data,
    }
    with (session_dir / "events.jsonl").open("a", encoding="utf-8") as file:
        file.write(json.dumps(record, ensure_ascii=False) + "\n")


def read_session_log(session_id: str | None) -> dict[str, Any]:
    session_dir = resolve_session_dir(session_id)
    events_path = session_dir / "events.jsonl"
    events: list[dict[str, Any]] = []
    if events_path.exists():
        with events_path.open("r", encoding="utf-8") as file:
            for line in file:
                stripped = line.strip()
                if stripped:
                    events.append(json.loads(stripped))
    return {"sessionId": session_dir.name, "events": events}


def clear_logs(session_id: str | None) -> None:
    if session_id:
        session_dir = resolve_session_dir(session_id)
        if session_dir.exists():
            shutil.rmtree(session_dir)
        return
    if SESSION_ROOT.exists():
        shutil.rmtree(SESSION_ROOT)


def save_audio_bytes(
    session_id: Any, file_name: Any, data: bytes, *, folder: str
) -> dict[str, Any]:
    """Write already-decoded audio bytes into ``<session>/<folder>/<name>``.
    Shared by the base64 (realtime chunk) and raw-binary (file upload) paths."""
    session_dir = resolve_session_dir(session_id)
    target_dir = session_dir / folder
    target_dir.mkdir(parents=True, exist_ok=True)
    name = safe_filename(str(file_name or "upload.webm"))
    path = target_dir / name
    path.write_bytes(data)
    return {"ok": True, "path": str(path), "bytes": len(data)}


def save_audio_blob(payload: dict[str, Any], *, folder: str) -> dict[str, Any]:
    """base64-in-JSON save path. Used by realtime recording chunks, which are
    small. File uploads use the raw-binary path instead (no 33% base64 inflation)."""
    raw_name = str(payload.get("fileName") or f"audio_{payload.get('chunkIndex', 'upload')}.webm")
    audio_base64 = str(payload.get("audioBase64") or "")
    if "," in audio_base64:
        audio_base64 = audio_base64.split(",", 1)[1]
    data = base64.b64decode(audio_base64)
    return save_audio_bytes(payload.get("sessionId"), raw_name, data, folder=folder)


# A [MM:SS] marker at the start of a line opens a new eval window; minutes may be
# 3+ digits for long meetings (e.g. [113:20]). Text after the marker (and any
# following non-marker lines) is that window's content.
_TIMESTAMP_LINE_RE = re.compile(r"^\[(\d+):([0-5]\d)\][ \t]?(.*)$")
# Copilot alerts are embedded as <ALERT>..</ALERT> blocks after their window; the
# replay/分析 path strips them so they are never re-fed to the coach as transcript.
_ALERT_BLOCK_RE = re.compile(r"<ALERT>.*?</ALERT>", re.DOTALL)


def format_mmss(total_seconds: float) -> str:
    total = max(0, int(total_seconds))
    return f"[{total // 60:02d}:{total % 60:02d}]"


def format_alert_block(alert: dict[str, Any]) -> str:
    """Render a Copilot alert as an <ALERT>..</ALERT> block — the same fields the web
    card shows. The wrapper lets the diagnostic transcript carry the alerts inline
    while parse_timestamped_transcript skips them on replay (they're output, not input)."""
    header = " · ".join(
        str(part)
        for part in (f"{alert.get('elapsedMinutes', 0)} 分钟", alert.get("priority"), alert.get("type"))
        if part
    )
    lines = ["<ALERT>", header]
    if alert.get("message"):
        lines.append(str(alert["message"]))
    if alert.get("suggested_question"):
        lines.append(f"建议提问：{alert['suggested_question']}")
    if alert.get("reason"):
        lines.append(f"理由：{alert['reason']}")
    lines.append("</ALERT>")
    return "\n".join(lines)


def format_timestamped_transcript(
    timeline: list[tuple[float, str]], alerts: list[dict[str, Any]] | None = None
) -> str:
    """Render eval windows as a [MM:SS] transcript. Window text keeps its newlines
    so replaying it feeds the coach byte-for-byte what the realtime run saw. Any
    alert produced for a window is appended right after it as an <ALERT> block,
    keyed by the window's elapsedSeconds."""
    by_window: dict[float, list[dict[str, Any]]] = {}
    for alert in alerts or []:
        seconds = alert.get("elapsedSeconds")
        if seconds is None:
            continue
        by_window.setdefault(round(float(seconds), 2), []).append(alert)
    lines: list[str] = []
    for seconds, text in timeline:
        lines.append(f"{format_mmss(seconds)} {text}")
        for alert in by_window.get(round(float(seconds), 2), []):
            lines.append(format_alert_block(alert))
    return "\n".join(lines)


def write_upload_transcripts(
    session_dir: Path,
    transcript_parts: list[str],
    eval_timeline: list[tuple[float, str]],
    alerts: list[dict[str, Any]] | None = None,
) -> None:
    """Persist the plain + [MM:SS] transcripts. Called eagerly on early termination
    (so a download right after 提前终止 already has them) and again at normal end."""
    session_dir.mkdir(parents=True, exist_ok=True)
    (session_dir / "uploaded_audio_transcript.txt").write_text(
        "\n".join(transcript_parts), encoding="utf-8"
    )
    (session_dir / "uploaded_audio_transcript_timestamped.txt").write_text(
        format_timestamped_transcript(eval_timeline, alerts), encoding="utf-8"
    )


def parse_timestamped_transcript(transcript: str) -> list[tuple[int, str]] | None:
    """Parse a [MM:SS] transcript into (elapsedSeconds, text) windows in order.

    Returns ``None`` when the transcript carries no [MM:SS] markers, so callers can
    fall back to plain char-chunking for hand-pasted transcripts.
    """
    # Drop <ALERT>..</ALERT> blocks: they are Copilot output the diagnostic
    # transcript carries inline, not transcript to re-feed the coach on replay.
    transcript = _ALERT_BLOCK_RE.sub("", transcript)
    segments: list[tuple[int, list[str]]] = []
    current_seconds: int | None = None
    current_lines: list[str] = []
    for line in transcript.splitlines():
        match = _TIMESTAMP_LINE_RE.match(line)
        if match:
            if current_seconds is not None:
                segments.append((current_seconds, current_lines))
            current_seconds = int(match.group(1)) * 60 + int(match.group(2))
            current_lines = [match.group(3)] if match.group(3) else []
        elif current_seconds is not None:
            current_lines.append(line)
    if current_seconds is not None:
        segments.append((current_seconds, current_lines))
    if not segments:
        return None
    windows = [(seconds, "\n".join(lines).strip()) for seconds, lines in segments]
    return [(seconds, text) for seconds, text in windows if text]


def coach_transcript(payload: dict[str, Any]) -> dict[str, Any]:
    session_id = payload.get("sessionId")
    transcript = str(payload.get("transcript", ""))
    config = dict(payload.get("config") or {})
    if not transcript.strip():
        raise ValueError("Transcript is empty.")

    append_event(session_id, "coach_transcript_started", {"transcriptChars": len(transcript)})
    result = evaluate_transcript_for_coach(session_id, transcript, config)
    append_event(
        session_id,
        "coach_transcript_completed",
        {"chunks": len(result["debugRecords"]), "alerts": len(result["alerts"])},
    )
    return result


def evaluate_transcript_for_coach(session_id: str | None, transcript: str, config: dict[str, Any]) -> dict[str, Any]:
    coach = create_coach(config)
    state = MeetingState(first_seen=datetime.now())
    alerts: list[dict[str, Any]] = []
    debug_records: list[dict[str, Any]] = []
    llm_errors: list[str] = []
    chars_per_step = int(config.get("charsPerStep") or 800)
    interval_seconds = int(config.get("uploadIntervalSeconds") or 60)

    timestamped = parse_timestamped_transcript(transcript)
    if timestamped is not None:
        # Replay an exported [MM:SS] transcript: reuse the exact windows and the
        # same elapsed-minute formula as the audio path so Copilot behaves
        # identically to the recording/audio-file run that produced this file.
        steps = [(text, max(1, int(seconds // 60))) for seconds, text in timestamped]
    else:
        # Hand-pasted transcript with no timing: approximate by char-chunking and
        # advancing one upload interval per chunk.
        steps = [
            (chunk, max(1, int((index * interval_seconds) / 60)))
            for index, chunk in enumerate(chunk_text(transcript, chars_per_step), start=1)
        ]

    for new_text, elapsed_minutes in steps:
        state_before = snapshot_state(state)
        alert = coach.evaluate(state, new_text, elapsed_minutes=elapsed_minutes)
        llm_error = getattr(coach, "last_error", None)
        if llm_error:
            llm_errors.append(str(llm_error))
        debug_record = build_coach_debug_record(
            coach=coach,
            state_before=state_before,
            new_text=new_text,
            elapsed_minutes=elapsed_minutes,
            alert=alert,
        )
        record_payload = asdict(debug_record)
        if llm_error:
            record_payload["llm_error"] = llm_error
        debug_records.append(record_payload)
        if alert:
            alerts.append({"elapsedMinutes": elapsed_minutes, **asdict(alert)})

    session_dir = resolve_session_dir(session_id)
    session_dir.mkdir(parents=True, exist_ok=True)
    (session_dir / "coach_transcript_debug.json").write_text(
        json.dumps(debug_records, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    if llm_errors:
        unique_errors = sorted(set(llm_errors))
        append_event(
            session_id,
            "llm_errors",
            {"count": len(llm_errors), "uniqueErrors": unique_errors[:5]},
        )
    return {"ok": True, "alerts": alerts, "debugRecords": debug_records}


async def coach_uploaded_audio(payload: dict[str, Any]) -> dict[str, Any]:
    session_id = payload.get("sessionId")
    config = dict(payload.get("config") or {})
    path = Path(str(payload.get("path") or ""))
    if not path.exists() or not path.is_file():
        raise ValueError("Uploaded audio file does not exist.")

    cancel_event = register_coach_upload_cancel(session_id)
    append_event(
        session_id,
        "coach_upload_started",
        {
            "audioPath": str(path),
            "bytes": path.stat().st_size,
            "coachEngine": config.get("coachEngine", "rules"),
            "mode": "realtime_file_stream",
        },
    )
    volc = VolcAsrSettings(
        api_key=str(config.get("volcAsrApiKey", "")),
        resource_id=str(config.get("volcAsrResourceId") or "volc.seedasr.sauc.duration"),
        ws_url=str(config.get("volcAsrWsUrl") or "wss://openspeech.bytedance.com/api/v3/sauc/bigmodel_async"),
        language=str(config.get("volcAsrLanguage") or "zh-CN"),
    )
    append_event(session_id, "audio_decode_started", {"audioPath": str(path)})
    # Decode the whole file to 16 kHz mono PCM BEFORE opening the ASR socket.
    # Decoding is CPU-bound, so run it off the event loop; doing it up front
    # means no ASR session is open while we decode, so the server's 8 s
    # "waiting next packet" deadline (error 45000081) can never trigger.
    pcm = await asyncio.to_thread(decode_file_to_pcm16, path)
    append_event(
        session_id,
        "audio_decoded",
        {"pcmBytes": len(pcm), "durationSeconds": round(len(pcm) / 2 / TARGET_RATE, 1)},
    )
    # Volc SAUC is a streaming engine: it transcribes at the pace audio arrives.
    # Blasting the whole file at once overruns its buffer and it returns only the
    # first few seconds before closing (observed: 113 min -> 608 chars). So pace
    # packets at the audio's real-time rate. Decoding already happened above, so
    # the WS opens with PCM ready and the first packet ships immediately — the
    # 8 s "waiting next packet" deadline (45000081) still never triggers.
    chunks = iter_pcm_chunks(pcm, chunk_ms=200, realtime=True)
    append_event(session_id, "asr_stream_starting", {"chunkMs": 200, "realtime": True})
    engine = VolcAsrEngine(
        settings=volc,
        audio_chunks=chunks,
        final_wait_seconds=30.0,
        debug_callback=lambda event_type, data: append_event(session_id, event_type, data),
    )
    cursor = TranscriptCursor()
    transcript_parts: list[str] = []
    buffer: list[str] = []
    # One entry per eval window actually sent to the coach: (elapsedSeconds, text).
    # Exported as a [MM:SS] transcript so "逐字稿调试" can replay the exact same
    # windows and elapsed minutes the realtime coach saw.
    eval_timeline: list[tuple[float, str]] = []
    eval_queue: asyncio.Queue[dict[str, Any] | None] = asyncio.Queue()
    coach = create_coach(config)
    coach_state = MeetingState(first_seen=datetime.now())
    alerts: list[dict[str, Any]] = []
    debug_records: list[dict[str, Any]] = []
    llm_errors: list[str] = []
    interval_seconds = int(config.get("uploadIntervalSeconds") or 60)
    started = time.perf_counter()
    last_eval_elapsed = 0.0
    event_index = 0

    async def enqueue_eval(text: str, elapsed_seconds: float) -> None:
        cleaned = text.strip()
        if not cleaned:
            return
        elapsed_minutes = max(1, int(elapsed_seconds // 60))
        eval_timeline.append((elapsed_seconds, cleaned))
        await eval_queue.put(
            {
                "text": cleaned,
                "elapsedSeconds": elapsed_seconds,
                "elapsedMinutes": elapsed_minutes,
                "transcriptChars": sum(len(part) for part in transcript_parts),
            }
        )
        append_event(
            session_id,
            "llm_eval_enqueued",
            {
                "elapsedSeconds": round(elapsed_seconds, 1),
                "elapsedMinutes": elapsed_minutes,
                "chars": len(cleaned),
                "queueSize": eval_queue.qsize(),
            },
        )

    worker = asyncio.create_task(
        run_realtime_llm_worker(
            session_id=session_id,
            queue=eval_queue,
            coach=coach,
            state=coach_state,
            alerts=alerts,
            debug_records=debug_records,
            llm_errors=llm_errors,
        )
    )

    cancelled = False
    try:
        async for event in engine.transcribe():
            if cancel_event.is_set():
                cancelled = True
                # Persist transcripts up to *now* before the queue drain (which can
                # block on an in-flight LLM eval), so a diagnostics download right
                # after 提前终止 already contains the [MM:SS] transcript.
                write_upload_transcripts(resolve_session_dir(session_id), transcript_parts, eval_timeline, alerts)
                append_event(session_id, "coach_upload_cancelled", {"events": event_index})
                break
            event_index += 1
            new_text = cursor.diff(event.text).strip()
            if not new_text:
                continue
            transcript_parts.append(new_text)
            buffer.append(new_text)
            elapsed_seconds = time.perf_counter() - started
            if event_index == 1:
                append_event(session_id, "asr_first_text", {"chars": len(new_text), "preview": new_text[:80]})
            if event_index == 1 or event_index % 10 == 0:
                append_event(
                    session_id,
                    "asr_progress",
                    {"events": event_index, "transcriptChars": sum(len(part) for part in transcript_parts)},
                )

            buffered_text = "\n".join(buffer)
            if elapsed_seconds - last_eval_elapsed >= interval_seconds and len(buffered_text.strip()) >= 50:
                await enqueue_eval(buffered_text, elapsed_seconds)
                buffer.clear()
                last_eval_elapsed = elapsed_seconds
    finally:
        # On early termination we stop feeding the coach (no final flush);
        # otherwise run a last eval on the trailing buffer. Either way we drain
        # whatever is already queued so the worker shuts down cleanly and the
        # partial transcript/alerts are complete up to the stop point.
        if buffer and not cancelled:
            await enqueue_eval("\n".join(buffer), time.perf_counter() - started)
        await eval_queue.join()
        await eval_queue.put(None)
        await worker
        clear_coach_upload_cancel(session_id)

    session_dir = resolve_session_dir(session_id)
    transcript = "\n".join(transcript_parts)
    write_upload_transcripts(session_dir, transcript_parts, eval_timeline, alerts)
    append_event(
        session_id,
        "asr_completed",
        {"events": event_index, "transcriptChars": len(transcript), "cancelled": cancelled},
    )
    if llm_errors:
        unique_errors = sorted(set(llm_errors))
        append_event(session_id, "llm_errors", {"count": len(llm_errors), "uniqueErrors": unique_errors[:5]})
    (session_dir / "uploaded_audio_debug.json").write_text(
        json.dumps(debug_records, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    append_event(
        session_id,
        "coach_upload_completed",
        {
            "audioPath": str(path),
            "transcriptChars": len(transcript),
            "alerts": len(alerts),
            "cancelled": cancelled,
        },
    )
    return {
        "ok": True,
        "cancelled": cancelled,
        "transcript": transcript,
        "alerts": alerts,
        "debugRecords": debug_records,
    }


async def run_realtime_llm_worker(
    *,
    session_id: str | None,
    queue: asyncio.Queue[dict[str, Any] | None],
    coach: Any,
    state: MeetingState,
    alerts: list[dict[str, Any]],
    debug_records: list[dict[str, Any]],
    llm_errors: list[str],
) -> None:
    while True:
        job = await queue.get()
        try:
            if job is None:
                return
            text = str(job["text"])
            elapsed_minutes = int(job["elapsedMinutes"])
            elapsed_seconds = float(job.get("elapsedSeconds", elapsed_minutes * 60))
            state_before = snapshot_state(state)
            append_event(
                session_id,
                "llm_eval_started",
                {
                    "elapsedMinutes": elapsed_minutes,
                    "chars": len(text),
                    "queueSize": queue.qsize(),
                },
            )
            started = time.perf_counter()
            alert = await asyncio.to_thread(coach.evaluate, state, text, elapsed_minutes)
            duration_ms = int((time.perf_counter() - started) * 1000)
            llm_error = getattr(coach, "last_error", None)
            if llm_error:
                llm_errors.append(str(llm_error))
            debug_record = build_coach_debug_record(
                coach=coach,
                state_before=state_before,
                new_text=text,
                elapsed_minutes=elapsed_minutes,
                alert=alert,
            )
            record_payload = asdict(debug_record)
            if llm_error:
                record_payload["llm_error"] = llm_error
            debug_records.append(record_payload)

            event_payload: dict[str, Any] = {
                "elapsedMinutes": elapsed_minutes,
                "durationMs": duration_ms,
                "alert": bool(alert),
                "error": llm_error,
            }
            if alert:
                alert_payload = {
                    "elapsedMinutes": elapsed_minutes,
                    "elapsedSeconds": round(elapsed_seconds, 2),
                    **asdict(alert),
                }
                alerts.append(alert_payload)
                append_event(session_id, "coach_alert", alert_payload)
            append_event(session_id, "llm_eval_done", event_payload)
        finally:
            queue.task_done()


def create_coach(config: dict[str, Any]) -> Any:
    engine = str(config.get("coachEngine") or "rules")
    meeting_duration = int(config.get("meetingDurationMinutes") or 90)
    if engine == "llm":
        return LLMRealtimeCoach(
            Settings(
                api_key=str(config.get("deepseekApiKey", "")),
                base_url=str(config.get("deepseekBaseUrl") or "https://api.deepseek.com"),
                model=str(config.get("deepseekModel") or "deepseek-v4-pro"),
                temperature=0.0,
            ),
            meeting_duration_minutes=meeting_duration,
            system_prompt=str(config.get("prompt") or "").strip() or None,
        )
    return RealtimeSalesCoach(meeting_duration_minutes=meeting_duration)


def resolve_session_dir(session_id: Any) -> Path:
    if not session_id:
        SESSION_ROOT.mkdir(parents=True, exist_ok=True)
        existing = sorted([p for p in SESSION_ROOT.iterdir() if p.is_dir()], reverse=True)
        if existing:
            return existing[0]
        return SESSION_ROOT / "manual"
    safe_id = safe_filename(str(session_id))
    return SESSION_ROOT / safe_id


def sanitize_config(value: Any) -> Any:
    if isinstance(value, dict):
        result: dict[str, Any] = {}
        for key, item in value.items():
            lowered = str(key).lower()
            if any(secret_word in lowered for secret_word in ("key", "secret", "token", "password")):
                result[key] = mask_secret(str(item))
            else:
                result[key] = sanitize_config(item)
        return result
    if isinstance(value, list):
        return [sanitize_config(item) for item in value]
    return value


def mask_secret(value: str) -> str:
    if not value:
        return ""
    if len(value) <= 8:
        return "***"
    return value[:4] + "..." + value[-4:]


def safe_filename(name: str) -> str:
    cleaned = re.sub(r"[^A-Za-z0-9._-]+", "_", name.strip())
    return cleaned.strip("._") or "file"


def first(values: list[str] | None) -> str | None:
    return values[0] if values else None


def now_iso() -> str:
    return datetime.now(timezone.utc).astimezone().isoformat(timespec="seconds")


def run(host: str, port: int) -> None:
    SESSION_ROOT.mkdir(parents=True, exist_ok=True)
    server = ThreadingHTTPServer((host, port), WebRequestHandler)
    url = f"http://{host}:{port}"
    print(f"Sales Retro Web running at {url}", flush=True)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("\nStopping Sales Retro Web.", flush=True)
    finally:
        server.server_close()


def main() -> None:
    parser = argparse.ArgumentParser(description="Run the local Sales Retro Web app.")
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=8765)
    args = parser.parse_args()
    run(args.host, args.port)


if __name__ == "__main__":
    main()
