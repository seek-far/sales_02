"""Copilot alerts are embedded in the [MM:SS] transcript as <ALERT> blocks, placed
right after the window that produced them, and ignored when the 逐字稿分析 replays
the transcript (output, not input)."""

from __future__ import annotations

from sales_retro_agent.web import (
    format_timestamped_transcript,
    parse_timestamped_transcript,
)


def _alert(seconds, minutes, **kw):
    return {"elapsedSeconds": seconds, "elapsedMinutes": minutes, **kw}


def test_alert_block_follows_its_window():
    timeline = [(60.5, "客户提到预算有限"), (121.2, "继续谈实施周期")]
    alerts = [
        _alert(
            121.2, 2, priority="high", type="objection_unhandled",
            message="客户的预算异议还没处理", suggested_question="预算大概在什么范围？", reason="出现价格顾虑",
        )
    ]
    out = format_timestamped_transcript(timeline, alerts)
    lines = out.splitlines()
    # Window 1 has no alert; window 2 is immediately followed by its <ALERT> block.
    assert lines[0] == "[01:00] 客户提到预算有限"
    assert lines[1] == "[02:01] 继续谈实施周期"
    assert lines[2] == "<ALERT>"
    assert "</ALERT>" in out
    assert "2 分钟 · high · objection_unhandled" in out
    assert "建议提问：预算大概在什么范围？" in out


def test_replay_ignores_alert_blocks():
    timeline = [(60.5, "甲说的话"), (121.2, "乙说的话")]
    alerts = [_alert(60.5, 1, priority="medium", type="x", message="一条提醒", reason="r")]
    with_alerts = format_timestamped_transcript(timeline, alerts)
    without_alerts = format_timestamped_transcript(timeline, None)

    # Parsing the alert-annotated transcript yields exactly the same windows as the
    # clean one — the <ALERT> content never leaks into the coach's input.
    assert parse_timestamped_transcript(with_alerts) == parse_timestamped_transcript(without_alerts)
    parsed = parse_timestamped_transcript(with_alerts)
    assert parsed == [(60, "甲说的话"), (121, "乙说的话")]
    # The alert text must not survive into any parsed window.
    assert all("一条提醒" not in text and "ALERT" not in text for _, text in parsed)


def test_no_alerts_is_unchanged_format():
    timeline = [(0.0, "开场白")]
    assert format_timestamped_transcript(timeline) == "[00:00] 开场白"


def test_alert_without_elapsed_seconds_is_skipped():
    # Defensive: an alert missing elapsedSeconds can't be placed, so it's dropped
    # from the transcript rather than mis-attributed.
    timeline = [(30.0, "一句话")]
    out = format_timestamped_transcript(timeline, [{"elapsedMinutes": 1, "message": "无定位"}])
    assert out == "[00:30] 一句话"
