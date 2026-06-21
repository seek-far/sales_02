"""Root-fix for the timestamped/plain transcript duplication.

Volc streaming emits cumulative result.text that gets *rewritten* when an
utterance finalizes (ITN: "90"->"九十", punctuation), which made TranscriptCursor
re-append whole sentences. UtteranceAccumulator commits only definite utterances
(keyed by stable start/end times) so the source is append-only and dup-free, and
labels speaker turns from additions.speaker_id.

Fixtures mirror frames captured from a real run (asr_raw_payload events).
"""

from __future__ import annotations

from sales_retro_agent.asr_volc import UtteranceAccumulator, utterances_of
from sales_retro_agent.text_diff import TranscriptCursor


def _payload(utterances):
    return {"result": {"text": "".join(u["text"] for u in utterances), "utterances": utterances}}


def test_no_duplication_across_itn_rewrite():
    # Provisional partial uses "90"; on finalize the engine rewrites it to "九十".
    frames = [
        [{"text": "我们约了90", "definite": False, "start_time": 0, "end_time": 1000}],
        [{"text": "我们约了90分钟", "definite": False, "start_time": 0, "end_time": 1200}],
        [{  # finalized + ITN rewrite, prefix changed (90 -> 九十)
            "text": "我们约了九十分钟。", "definite": True, "start_time": 0, "end_time": 1200,
            "additions": {"speaker_id": "0"},
        }],
        [
            {"text": "我们约了九十分钟。", "definite": True, "start_time": 0, "end_time": 1200,
             "additions": {"speaker_id": "0"}},
            {"text": "先用十分钟", "definite": False, "start_time": 1300, "end_time": 2000},
        ],
    ]
    acc = UtteranceAccumulator(speaker_labels=True)
    emitted = []
    for f in frames:
        text = acc.update(utterances_of(_payload(f)))
        if text and (not emitted or text != emitted[-1]):
            emitted.append(text)

    final = emitted[-1]
    # The rewritten sentence appears exactly once — not duplicated.
    assert final.count("九十分钟") == 1
    assert "我们约了90" not in final  # provisional text is never committed
    # Speaker label present.
    assert final == "[说话人0] 我们约了九十分钟。"


def test_emitted_stream_is_append_only_for_cursor():
    # Each emit must be a pure prefix-extension of the last, so TranscriptCursor
    # only ever returns appended text (never re-emits a whole sentence).
    frames = [
        [{"text": "甲句。", "definite": True, "start_time": 0, "end_time": 10, "additions": {"speaker_id": "0"}}],
        [
            {"text": "甲句。", "definite": True, "start_time": 0, "end_time": 10, "additions": {"speaker_id": "0"}},
            {"text": "乙句。", "definite": True, "start_time": 11, "end_time": 20, "additions": {"speaker_id": "1"}},
        ],
    ]
    acc = UtteranceAccumulator(speaker_labels=True)
    cursor = TranscriptCursor()
    deltas = []
    for f in frames:
        text = acc.update(utterances_of(_payload(f)))
        deltas.append(cursor.diff(text))
    rebuilt = "".join(deltas)
    assert rebuilt == acc.render()  # diffs concatenate back to the full text
    assert rebuilt.count("甲句。") == 1 and rebuilt.count("乙句。") == 1
    # Speaker change inserts a label for the second speaker.
    assert "[说话人1] 乙句。" in acc.render()


def test_cumulative_list_does_not_double_commit():
    # The same definite utterance reappears in later frames' lists; commit once.
    u0 = {"text": "句子。", "definite": True, "start_time": 0, "end_time": 5, "additions": {"speaker_id": "0"}}
    acc = UtteranceAccumulator()
    acc.update([u0])
    acc.update([u0])
    acc.update([u0, {"text": "在说", "definite": False, "start_time": 6, "end_time": 9}])
    assert acc.render().count("句子。") == 1


def test_flush_partial_recovers_unfinalized_tail():
    acc = UtteranceAccumulator(speaker_labels=False)
    acc.update([{"text": "已定稿。", "definite": True, "start_time": 0, "end_time": 5}])
    acc.update([
        {"text": "已定稿。", "definite": True, "start_time": 0, "end_time": 5},
        {"text": "没说完的尾巴", "definite": False, "start_time": 6, "end_time": 9},
    ])
    assert "没说完的尾巴" not in acc.render()      # not committed while provisional
    assert "没说完的尾巴" in acc.flush_partial()    # recovered at stream end


def test_speaker_labels_can_be_disabled():
    acc = UtteranceAccumulator(speaker_labels=False)
    text = acc.update([{"text": "无标签。", "definite": True, "start_time": 0, "end_time": 5,
                        "additions": {"speaker_id": "0"}}])
    assert text == "无标签。"


def test_utterances_of_falls_back_when_absent():
    assert utterances_of({"result": {"text": "abc"}}) is None
    assert utterances_of({"text": "abc"}) is None
    assert utterances_of(None) is None
    assert utterances_of({"result": {"utterances": []}}) == []
