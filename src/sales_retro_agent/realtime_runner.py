from __future__ import annotations

import asyncio
import time
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path

from .asr_types import AsrEngine
from .coach_debug import append_coach_debug_record, build_coach_debug_record, snapshot_state
from .realtime_coach import MeetingState, RealtimeSalesCoach
from .text_diff import TranscriptCursor


MIN_FAST_FORWARD_EVAL_CHARS = 10
MAX_REALTIME_EVAL_CHARS = 6000


@dataclass(frozen=True)
class CoachEvalJob:
    text: str
    elapsed_seconds: float
    elapsed_minutes: int
    seen_transcript: str


async def run_realtime_coach(
    engine: AsrEngine,
    coach: RealtimeSalesCoach,
    *,
    coach_interval_seconds: int = 60,
    alert_context_chars: int = 500,
    print_recent_transcript: bool = True,
    fast_forward_time: bool = False,
    transcript_output_path: Path | None = None,
    coach_debug_path: Path | None = None,
    max_alerts: int | None = None,
    max_events: int | None = None,
) -> None:
    started_at = datetime.now()
    state = MeetingState(first_seen=started_at)
    buffer: list[str] = []
    last_eval = started_at
    events = 0
    synthetic_elapsed_seconds = 0
    seen_transcript = ""
    transcript_cursor = TranscriptCursor()
    eval_queue: asyncio.Queue[CoachEvalJob | None] = asyncio.Queue()
    stop_event = asyncio.Event()

    if transcript_output_path:
        transcript_output_path.parent.mkdir(parents=True, exist_ok=True)
        transcript_output_path.write_text("", encoding="utf-8")

    async def enqueue_eval(text: str, elapsed_seconds: float, transcript_snapshot: str) -> None:
        original_chars = len(text)
        text = clamp_eval_text(text)
        if len(text) != original_chars:
            print_timing(
                "llm_window_clamped",
                elapsed_seconds=elapsed_seconds,
                original_chars=original_chars,
                chars=len(text),
            )
        elapsed_minutes = max(0, int(elapsed_seconds // 60))
        await eval_queue.put(
            CoachEvalJob(
                text=text,
                elapsed_seconds=elapsed_seconds,
                elapsed_minutes=elapsed_minutes,
                seen_transcript=transcript_snapshot,
            )
        )
        print_timing(
            "llm_enqueue",
            elapsed_seconds=elapsed_seconds,
            chars=len(text),
            queue_size=eval_queue.qsize(),
        )

    worker = asyncio.create_task(
        run_coach_eval_worker(
            eval_queue,
            coach,
            state,
            print_recent_transcript=print_recent_transcript,
            alert_context_chars=alert_context_chars,
            coach_debug_path=coach_debug_path,
            max_alerts=max_alerts,
            stop_event=stop_event,
        ),
        name="coach-eval-worker",
    )

    try:
        async for event in engine.transcribe():
            if stop_event.is_set():
                break
            events += 1
            new_text = transcript_cursor.diff(event.text).strip()
            if not new_text:
                continue

            now = datetime.now()
            if fast_forward_time:
                synthetic_elapsed_seconds += coach_interval_seconds
                elapsed_seconds = synthetic_elapsed_seconds
            else:
                elapsed_seconds = (now - started_at).total_seconds()

            buffer.append(new_text)
            seen_transcript = append_text(seen_transcript, new_text)
            print_timing(
                "asr_event",
                elapsed_seconds=elapsed_seconds,
                chars=len(new_text),
                events=events,
                buffer_chars=sum(len(part) for part in buffer),
            )
            if transcript_output_path:
                append_transcript_file(transcript_output_path, new_text, elapsed_seconds=elapsed_seconds)

            should_eval = (now - last_eval).total_seconds() >= coach_interval_seconds
            if max_events is not None:
                should_eval = True

            if should_eval and buffer:
                recent_text = "\n".join(buffer)
                if fast_forward_time and not has_enough_eval_context(recent_text):
                    if max_events is not None and events >= max_events:
                        break
                    continue
                await enqueue_eval(recent_text, elapsed_seconds, seen_transcript)
                buffer.clear()
                last_eval = now

            if max_events is not None and events >= max_events:
                break
    finally:
        if buffer and not stop_event.is_set():
            elapsed_seconds = synthetic_elapsed_seconds if fast_forward_time else (datetime.now() - started_at).total_seconds()
            final_text = "\n".join(buffer)
            if not fast_forward_time or has_enough_eval_context(final_text):
                await enqueue_eval(final_text, elapsed_seconds, seen_transcript)
        await eval_queue.put(None)
        await worker


async def run_coach_eval_worker(
    eval_queue: asyncio.Queue[CoachEvalJob | None],
    coach: RealtimeSalesCoach,
    state: MeetingState,
    *,
    print_recent_transcript: bool,
    alert_context_chars: int,
    coach_debug_path: Path | None,
    max_alerts: int | None,
    stop_event: asyncio.Event,
) -> None:
    alerts = 0
    while True:
        job = await eval_queue.get()
        if job is None:
            return

        if print_recent_transcript:
            print_recent_transcript_block(job.text, elapsed_minutes=job.elapsed_minutes)
        state_before = snapshot_state(state)
        started = time.perf_counter()
        print_timing(
            "llm_start",
            elapsed_seconds=job.elapsed_seconds,
            chars=len(job.text),
            queue_size=eval_queue.qsize(),
        )
        alert = await asyncio.to_thread(coach.evaluate, state, job.text, job.elapsed_minutes)
        duration_ms = int((time.perf_counter() - started) * 1000)
        print_timing(
            "llm_done",
            elapsed_seconds=job.elapsed_seconds,
            duration_ms=duration_ms,
            alert=bool(alert),
            queue_size=eval_queue.qsize(),
        )
        if coach_debug_path:
            append_coach_debug_record(
                coach_debug_path,
                build_coach_debug_record(
                    coach=coach,
                    state_before=state_before,
                    new_text=job.text,
                    elapsed_minutes=job.elapsed_minutes,
                    alert=alert,
                ),
            )
        if alert:
            alerts += 1
            print_alert_context(job.seen_transcript, chars=alert_context_chars)
            print_alert(alerts, alert, elapsed_seconds=job.elapsed_seconds)
            if max_alerts is not None and alerts >= max_alerts:
                stop_event.set()
                drain_eval_queue(eval_queue)
                return


def run_transcript_coach_debug(
    transcript: str,
    coach: RealtimeSalesCoach,
    *,
    chars_per_step: int = 500,
    alert_context_chars: int = 500,
    minutes_per_step: int = 1,
    max_alerts: int | None = None,
    print_recent_transcript: bool = True,
    coach_debug_path: Path | None = None,
) -> None:
    state = MeetingState(first_seen=datetime.now())
    alerts = 0
    seen_transcript = ""

    for step_index, chunk in enumerate(chunk_text(transcript, chars_per_step), start=1):
        seen_transcript = f"{seen_transcript}\n{chunk}".strip() if seen_transcript else chunk
        elapsed_minutes = step_index * minutes_per_step
        if print_recent_transcript:
            print_recent_transcript_block(chunk, elapsed_minutes=elapsed_minutes)

        state_before = snapshot_state(state)
        alert = coach.evaluate(state, chunk, elapsed_minutes=elapsed_minutes)
        if coach_debug_path:
            append_coach_debug_record(
                coach_debug_path,
                build_coach_debug_record(
                    coach=coach,
                    state_before=state_before,
                    new_text=chunk,
                    elapsed_minutes=elapsed_minutes,
                    alert=alert,
                ),
            )
        if alert:
            alerts += 1
            print_alert_context(seen_transcript, chars=alert_context_chars)
            print_alert(alerts, alert, elapsed_seconds=elapsed_minutes * 60)
            if max_alerts is not None and alerts >= max_alerts:
                return


def chunk_text(text: str, chars_per_step: int) -> list[str]:
    if chars_per_step <= 0:
        raise ValueError("chars_per_step must be positive.")

    normalized = text.strip()
    if not normalized:
        return []

    chunks: list[str] = []
    current: list[str] = []
    current_len = 0

    for line in normalized.splitlines():
        line_len = len(line) + 1
        if current and current_len + line_len > chars_per_step:
            chunks.append("\n".join(current).strip())
            current = [line]
            current_len = line_len
        else:
            current.append(line)
            current_len += line_len

    if current:
        chunks.append("\n".join(current).strip())
    return chunks


def has_enough_eval_context(text: str) -> bool:
    compact = "".join(text.split())
    return len(compact) >= MIN_FAST_FORWARD_EVAL_CHARS


def clamp_eval_text(text: str, *, max_chars: int = MAX_REALTIME_EVAL_CHARS) -> str:
    if max_chars <= 0 or len(text) <= max_chars:
        return text
    return text[-max_chars:]


def drain_eval_queue(eval_queue: asyncio.Queue[CoachEvalJob | None]) -> None:
    while True:
        try:
            eval_queue.get_nowait()
        except asyncio.QueueEmpty:
            return


def print_timing(event: str, **fields) -> None:  # noqa: ANN003
    parts = [f"{key}={value}" for key, value in fields.items()]
    suffix = " " + " ".join(parts) if parts else ""
    print(f"[TIMING {datetime.now().isoformat(timespec='seconds')}] {event}{suffix}", flush=True)


def print_recent_transcript_block(text: str, *, elapsed_minutes: int) -> None:
    print(
        "\n[RECENT TRANSCRIPT ~ last window | elapsed {elapsed}m]\n{text}\n".format(
            elapsed=elapsed_minutes,
            text=text.strip(),
        ),
        flush=True,
    )


def print_alert_context(text: str, *, chars: int) -> None:
    if chars <= 0:
        return
    context = text[-chars:].strip()
    if not context:
        return
    print(
        "\n[ALERT CONTEXT | previous {chars} chars]\n{text}\n".format(
            chars=chars,
            text=context,
        ),
        flush=True,
    )


def print_alert(index: int, alert, *, elapsed_seconds: float | int | None = None) -> None:  # noqa: ANN001
    timestamp = format_elapsed(elapsed_seconds) if elapsed_seconds is not None else "unknown"
    block = (
        "\n"
        + "=" * 72
        + "\n"
        "[COPILOT ALERT #{index} | t={timestamp}] priority={priority} type={type}\n"
        "{message}\n\n"
        "建议问：{question}\n"
        "原因：{reason}\n"
        + "=" * 72
        + "\n"
    ).format(
        index=index,
        timestamp=timestamp,
        priority=alert.priority.upper(),
        type=alert.type,
        message=alert.message,
        question=alert.suggested_question,
        reason=alert.reason,
    )
    print(block, flush=True)


def append_text(current: str, new_text: str) -> str:
    return f"{current}\n{new_text}".strip() if current else new_text


def append_transcript_file(path: Path, text: str, *, elapsed_seconds: float | int) -> None:
    with path.open("a", encoding="utf-8") as file:
        file.write(f"[{format_elapsed(elapsed_seconds)}] {text.strip()}\n")


def format_elapsed(seconds: float | int) -> str:
    total = max(0, int(seconds))
    hours, remainder = divmod(total, 3600)
    minutes, secs = divmod(remainder, 60)
    if hours:
        return f"{hours:02d}:{minutes:02d}:{secs:02d}"
    return f"{minutes:02d}:{secs:02d}"


def parse_timestamped_transcript(text: str) -> dict[int, str]:
    """Parse a timestamped ASR transcript into per-minute text chunks.

    Each line has format ``[MM:SS] text``. Lines are grouped by minute,
    then deduped against cumulative ASR roll-ups with TranscriptCursor.
    Returns ``{minute: new_text}`` for minutes that produced new content.
    """
    import re
    from collections import defaultdict

    # Strip Copilot <ALERT>..</ALERT> blocks — they are output embedded in the
    # diagnostic transcript, not transcript to re-feed the coach.
    text = re.sub(r"<ALERT>.*?</ALERT>", "", text, flags=re.DOTALL)
    by_minute: defaultdict[int, list[str]] = defaultdict(list)
    for line in text.splitlines():
        m = re.match(r"\[(\d+):(\d+)\]\s*(.*)", line)
        if m:
            mm = int(m.group(1))
            content = m.group(3).strip()
            if content:
                by_minute[mm].append(content)

    if not by_minute:
        return {}

    max_minute = max(by_minute.keys())
    cursor = TranscriptCursor()
    result: dict[int, str] = {}

    for minute in range(max_minute + 1):
        lines_in_minute = by_minute.get(minute, [])
        if not lines_in_minute:
            continue
        joined = "\n".join(lines_in_minute)
        new_text = cursor.diff(joined).strip()
        if new_text:
            result[minute] = new_text

    return result


def run_timestamped_coach(
    transcript_path: str,
    coach: RealtimeSalesCoach,
    *,
    alert_context_chars: int = 500,
    max_alerts: int | None = None,
    print_recent_transcript: bool = True,
    coach_debug_path: Path | None = None,
    encoding: str = "utf-8",
) -> None:
    """Run realtime Copilot coach against a timestamped transcript file.

    Parses ``[MM:SS] text`` lines, groups by minute, deduplicates cumulative
    ASR roll-ups, then evaluates the coach once per minute in fast-forward
    mode (virtual elapsed time)."""
    text = Path(transcript_path).read_text(encoding=encoding)
    minute_texts = parse_timestamped_transcript(text)

    if not minute_texts:
        print("No timestamped content found in transcript.", flush=True)
        return

    state = MeetingState(first_seen=datetime.now())
    alerts = 0
    seen_transcript = ""

    for minute in sorted(minute_texts.keys()):
        new_text = minute_texts[minute]
        seen_transcript = f"{seen_transcript}\n{new_text}".strip() if seen_transcript else new_text
        if print_recent_transcript:
            print_recent_transcript_block(new_text, elapsed_minutes=minute)

        state_before = snapshot_state(state)
        alert = coach.evaluate(state, new_text, elapsed_minutes=minute)
        if coach_debug_path:
            append_coach_debug_record(
                coach_debug_path,
                build_coach_debug_record(
                    coach=coach,
                    state_before=state_before,
                    new_text=new_text,
                    elapsed_minutes=minute,
                    alert=alert,
                ),
            )
        if alert:
            alerts += 1
            print_alert_context(seen_transcript, chars=alert_context_chars)
            print_alert(alerts, alert, elapsed_seconds=minute * 60)
            if max_alerts is not None and alerts >= max_alerts:
                return
