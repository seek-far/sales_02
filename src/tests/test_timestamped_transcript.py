"""Regression for the [MM:SS] timestamped transcript exported in the diagnostics
package.

Goal: replaying that file through 逐字稿调试 (`evaluate_transcript_for_coach`)
must drive Copilot with the *exact* same (text, elapsed_minutes) windows the
realtime audio/recording run saw — see `format_timestamped_transcript` /
`parse_timestamped_transcript` in web.py.
"""
from __future__ import annotations

from sales_retro_agent import web


def test_format_round_trips_through_parse() -> None:
    # Windows as the audio path records them: (elapsedSeconds, window text). Text
    # may be multi-line and meetings can run past 99 minutes ([113:20]).
    timeline = [
        (0.4, "客户说现在预测很麻烦"),
        (61.9, "第二窗\n多行内容"),
        (6800.0, "很久之后的尾段"),
    ]
    rendered = web.format_timestamped_transcript(timeline)
    assert rendered.splitlines()[0].startswith("[00:00] ")
    assert "[01:01] " in rendered
    assert "[113:20] " in rendered  # 6800s -> 113m20s, 3-digit minutes survive

    parsed = web.parse_timestamped_transcript(rendered)
    assert parsed is not None
    # Whole-second resolution; text (incl. newlines) preserved verbatim.
    assert parsed == [
        (0, "客户说现在预测很麻烦"),
        (61, "第二窗\n多行内容"),
        (6800, "很久之后的尾段"),
    ]


def test_write_upload_transcripts_creates_both_files(tmp_path) -> None:
    # Eager write on 提前终止 must drop both transcripts so a diagnostics download
    # right after cancel already contains the [MM:SS] file.
    timeline = [(0.0, "第一窗"), (62.0, "第二窗")]
    web.write_upload_transcripts(tmp_path, ["第一窗", "第二窗"], timeline)
    plain = tmp_path / "uploaded_audio_transcript.txt"
    stamped = tmp_path / "uploaded_audio_transcript_timestamped.txt"
    assert plain.read_text(encoding="utf-8") == "第一窗\n第二窗"
    assert stamped.read_text(encoding="utf-8") == "[00:00] 第一窗\n[01:02] 第二窗"


def test_plain_transcript_has_no_markers() -> None:
    # No [MM:SS] markers -> None, so callers fall back to char-chunking.
    assert web.parse_timestamped_transcript("就是一段没有时间戳的普通逐字稿") is None


def test_replay_drives_coach_like_audio_path(tmp_path, monkeypatch) -> None:
    monkeypatch.setattr(web, "resolve_session_dir", lambda session_id: tmp_path)

    # Same elapsed-minute formula the audio path uses in enqueue_eval.
    timeline = [
        (12.0, "开场寒暄"),
        (75.0, "客户担心准确率和安全"),
        (240.0, "聊到预算和采购流程"),
    ]
    expected = [(text, max(1, int(seconds // 60))) for seconds, text in timeline]

    transcript = web.format_timestamped_transcript(timeline)
    config = {"coachEngine": "rules", "meetingDurationMinutes": 90}
    result = web.evaluate_transcript_for_coach("sess", transcript, config)

    seen = [(record["new_text"], record["elapsed_minutes"]) for record in result["debugRecords"]]
    assert seen == expected
