"""Guard: the frontend 工作状态 lock + end-of-work 收尾 wiring stays in place.

There is no JS test runner in this thin-backend delivery, so these are string
guards (same spirit as test_web_static_packaged.py) over the shipped
backend.js / backend.html. They pin the invariants the feature relies on:

- a single busy predicate + lock function exist and are referenced;
- the end-of-work ceremony (finishWork) is hooked into all three "结束工作"
  paths (stop recording, audio-coach finally, transcript-coach finally);
- diagnostics export is awaitable (fetch+Blob), NOT a window.location navigation
  that the 收尾's clear could race;
- the finish dialog the ceremony awaits actually exists in the HTML.
"""

from __future__ import annotations

from pathlib import Path

WEB_STATIC = Path(__file__).resolve().parents[1] / "sales_retro_agent" / "web_static"
BACKEND_JS = (WEB_STATIC / "backend.js").read_text(encoding="utf-8")
BACKEND_HTML = (WEB_STATIC / "backend.html").read_text(encoding="utf-8")


def test_busy_predicate_covers_all_four_work_states():
    assert "function isBusy()" in BACKEND_JS
    for flag in ("state.recording", "state.uploading", "state.coachRunning", "state.transcriptRunning"):
        assert flag in BACKEND_JS, f"isBusy must consider {flag}"


def test_working_lock_freezes_tabs_config_and_clear():
    assert "function updateWorkingLock()" in BACKEND_JS
    # Tabs and 清除日志 are locked while busy.
    assert '.mode-tabs .tab' in BACKEND_JS
    assert 'clearLogButton").disabled = busy' in BACKEND_JS
    # Tab switching is also guarded in code, not only via the disabled attribute.
    assert "if (isBusy()) return;" in BACKEND_JS


def test_finish_work_hooked_into_all_three_end_paths():
    # Exactly the three 结束工作 transitions call finishWork (plus its definition).
    assert BACKEND_JS.count("await finishWork()") >= 3
    assert "async function finishWork()" in BACKEND_JS


def test_export_is_awaitable_not_a_navigation():
    # The 收尾 clears the session right after export; export must complete first,
    # so it can't be a fire-and-forget window.location navigation.
    assert "window.location.href" not in BACKEND_JS
    assert "async function downloadDiagnostics()" in BACKEND_JS
    assert "await downloadDiagnostics()" in BACKEND_JS


def test_clear_logs_alerts_are_opt_in():
    # Manual button keeps alerts (clearAlerts defaults false); 收尾 passes true.
    assert "function clearLogs({ clearAlerts = false } = {})" in BACKEND_JS
    assert "clearLogs({ clearAlerts: true })" in BACKEND_JS


def test_finish_dialog_exists_in_html():
    assert 'id="finishDialog"' in BACKEND_HTML
    assert "不导出将不保留本次结果" in BACKEND_HTML
    assert 'value="export"' in BACKEND_HTML
