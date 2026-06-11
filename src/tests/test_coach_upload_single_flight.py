"""Regression for the per-session single-flight guard on coach-upload runs.

Field bug: a slow base64 upload left the run button live with no progress, so
users double-clicked and launched concurrent coach-upload runs over the SAME
session. Each run paced audio on its own clock and wrote alerts into one log, so
the merged alerts came out non-monotonic (observed elapsedMinutes 2,8,2,2,7).

The fix disables the button on first click (frontend) and refuses a second
in-flight run per session (backend, guarded here).
"""
from __future__ import annotations

import threading

from sales_retro_agent import web


def _reset(session_id: str) -> None:
    web.end_coach_upload(session_id)


def test_second_run_for_same_session_is_rejected() -> None:
    sid = "sess-A"
    _reset(sid)
    assert web.try_begin_coach_upload(sid) is True
    # A concurrent second click for the same session must be refused.
    assert web.try_begin_coach_upload(sid) is False
    _reset(sid)


def test_session_can_run_again_after_it_ends() -> None:
    sid = "sess-B"
    _reset(sid)
    assert web.try_begin_coach_upload(sid) is True
    web.end_coach_upload(sid)
    # Once the previous run finished, the next run is allowed.
    assert web.try_begin_coach_upload(sid) is True
    _reset(sid)


def test_different_sessions_do_not_block_each_other() -> None:
    a, b = "sess-C", "sess-D"
    _reset(a)
    _reset(b)
    assert web.try_begin_coach_upload(a) is True
    # A different session is independent and may start while A is running.
    assert web.try_begin_coach_upload(b) is True
    _reset(a)
    _reset(b)


def test_empty_session_id_is_never_guarded() -> None:
    # No session id means nothing to key on; never block (and never crash).
    assert web.try_begin_coach_upload(None) is True
    assert web.try_begin_coach_upload("") is True
    web.end_coach_upload(None)  # must be a no-op, not raise


def test_guard_is_thread_safe_under_concurrent_begins() -> None:
    # ThreadingHTTPServer dispatches each request on its own thread, so the
    # guard must let exactly one of N racing begins win.
    sid = "sess-race"
    _reset(sid)
    wins: list[bool] = []
    wins_lock = threading.Lock()
    barrier = threading.Barrier(8)

    def worker() -> None:
        barrier.wait()
        won = web.try_begin_coach_upload(sid)
        with wins_lock:
            wins.append(won)

    threads = [threading.Thread(target=worker) for _ in range(8)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()

    assert sum(1 for w in wins if w) == 1
    _reset(sid)
