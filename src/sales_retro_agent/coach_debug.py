from __future__ import annotations

import json
from dataclasses import asdict, dataclass
from datetime import datetime
from pathlib import Path
from typing import Any, Iterable

from .realtime_coach import CoachAlert, MeetingState, RealtimeSalesCoach


@dataclass(frozen=True)
class CoachStateSnapshot:
    transcript: str
    confirmed: list[str]
    last_alert_by_type: dict[str, int]


@dataclass(frozen=True)
class CoachDebugRecord:
    timestamp: str
    elapsed_minutes: int
    meeting_duration_minutes: int
    min_minutes_between_same_type: int
    new_text: str
    state_before: CoachStateSnapshot
    alert: dict[str, Any] | None


def snapshot_state(state: MeetingState) -> CoachStateSnapshot:
    return CoachStateSnapshot(
        transcript=state.transcript,
        confirmed=sorted(state.confirmed),
        last_alert_by_type=dict(state.last_alert_by_type),
    )


def append_coach_debug_record(path: Path, record: CoachDebugRecord) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as file:
        file.write(json.dumps(asdict(record), ensure_ascii=False) + "\n")


def build_coach_debug_record(
    *,
    coach: Any,
    state_before: CoachStateSnapshot,
    new_text: str,
    elapsed_minutes: int,
    alert: CoachAlert | None,
) -> CoachDebugRecord:
    cooldown_minutes = int(
        getattr(coach, "min_minutes_between_same_type", getattr(coach, "_cooldown_minutes", 5))
    )
    return CoachDebugRecord(
        timestamp=datetime.now().isoformat(timespec="seconds"),
        elapsed_minutes=elapsed_minutes,
        meeting_duration_minutes=coach.meeting_duration_minutes,
        min_minutes_between_same_type=cooldown_minutes,
        new_text=new_text,
        state_before=state_before,
        alert=asdict(alert) if alert else None,
    )


def iter_coach_debug_records(path: Path) -> Iterable[CoachDebugRecord]:
    with path.open("r", encoding="utf-8") as file:
        for line_no, line in enumerate(file, start=1):
            stripped = line.strip()
            if not stripped:
                continue
            payload = json.loads(stripped)
            yield parse_coach_debug_record(payload, line_no=line_no)


def parse_coach_debug_record(payload: dict[str, Any], *, line_no: int = 0) -> CoachDebugRecord:
    state_payload = payload.get("state_before")
    if not isinstance(state_payload, dict):
        raise ValueError(f"Coach debug record line {line_no} must contain state_before object.")
    new_text = payload.get("new_text")
    if not isinstance(new_text, str):
        raise ValueError(f"Coach debug record line {line_no} must contain new_text string.")

    alert = payload.get("alert")
    if alert is not None and not isinstance(alert, dict):
        raise ValueError(f"Coach debug record line {line_no} contains invalid alert.")

    return CoachDebugRecord(
        timestamp=str(payload.get("timestamp", "")),
        elapsed_minutes=int(payload.get("elapsed_minutes", 0)),
        meeting_duration_minutes=int(payload.get("meeting_duration_minutes", 90)),
        min_minutes_between_same_type=int(payload.get("min_minutes_between_same_type", 5)),
        new_text=new_text,
        state_before=CoachStateSnapshot(
            transcript=str(state_payload.get("transcript", "")),
            confirmed=list(state_payload.get("confirmed", [])),
            last_alert_by_type=dict(state_payload.get("last_alert_by_type", {})),
        ),
        alert=alert,
    )


def restore_state(snapshot: CoachStateSnapshot) -> MeetingState:
    return MeetingState(
        transcript=snapshot.transcript,
        confirmed=set(snapshot.confirmed),
        last_alert_by_type={str(key): int(value) for key, value in snapshot.last_alert_by_type.items()},
    )


def replay_coach_record(record: CoachDebugRecord) -> CoachAlert | None:
    coach = RealtimeSalesCoach(
        meeting_duration_minutes=record.meeting_duration_minutes,
        min_minutes_between_same_type=record.min_minutes_between_same_type,
    )
    state = restore_state(record.state_before)
    return coach.evaluate(state, record.new_text, elapsed_minutes=record.elapsed_minutes)
