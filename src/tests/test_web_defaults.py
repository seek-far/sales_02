"""Regression for the LLM-only thin-backend deployment defaults.

Covers the three issues reported on the built installer:
1. default prompt must be served (non-empty),
2. upload run path must be the backend page (not the rules-only index),
3. the coach engine must default to LLM.
"""
from __future__ import annotations

import json
import threading
import urllib.request
from http.server import ThreadingHTTPServer

import pytest

from sales_retro_agent import web


@pytest.fixture()
def server():
    httpd = ThreadingHTTPServer(("127.0.0.1", 0), web.WebRequestHandler)
    port = httpd.server_address[1]
    t = threading.Thread(target=httpd.serve_forever, daemon=True)
    t.start()
    try:
        yield f"http://127.0.0.1:{port}"
    finally:
        httpd.shutdown()
        httpd.server_close()


def _get(url: str) -> str:
    with urllib.request.urlopen(url, timeout=5) as resp:
        return resp.read().decode("utf-8")


def test_default_config_is_llm_with_prompt() -> None:
    cfg = web.default_config()
    assert cfg["coachEngine"] == "llm"
    assert cfg["prompt"].strip(), "default prompt must be non-empty"


def test_root_serves_backend_not_pure_frontend(server: str) -> None:
    body = _get(server + "/")
    assert 'src="./backend.js"' in body  # backend.html loads the thin-backend script
    assert "提前终止" in body  # upload-mode early-termination button is present


def test_index_redirects_to_backend(server: str) -> None:
    body = _get(server + "/index.html")
    assert "./backend.html" in body
    assert "纯前端本地版" not in body  # old rules-only UI must be gone


def test_default_config_endpoint(server: str) -> None:
    data = json.loads(_get(server + "/api/default-config"))
    assert data["coachEngine"] == "llm"
    assert data["prompt"].strip()


def _post(url: str, payload: dict) -> dict:
    data = json.dumps(payload).encode("utf-8")
    req = urllib.request.Request(url, data=data, headers={"Content-Type": "application/json"})
    with urllib.request.urlopen(req, timeout=5) as resp:
        return json.loads(resp.read().decode("utf-8"))


def test_cancel_registry_round_trip() -> None:
    session_id = "test-cancel-session"
    web.clear_coach_upload_cancel(session_id)
    # No live run yet -> nothing to cancel.
    assert web.request_coach_upload_cancel(session_id) is False
    event = web.register_coach_upload_cancel(session_id)
    assert not event.is_set()
    # A live run exists -> cancel signals its event.
    assert web.request_coach_upload_cancel(session_id) is True
    assert event.is_set()
    web.clear_coach_upload_cancel(session_id)
    assert web.request_coach_upload_cancel(session_id) is False


def test_cancel_endpoint_reports_no_live_run(server: str) -> None:
    # Cancelling a session with no in-flight coach-upload is a no-op, not an error.
    result = _post(server + "/api/coach-upload/cancel", {"sessionId": "missing-session"})
    assert result == {"ok": True, "cancelled": False}
