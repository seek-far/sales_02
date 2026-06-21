"""Atomic export+clear for /api/diagnostics.

The end-of-work 收尾 exports the diagnostics bundle and then clears the session.
With the old two-request flow (download, then a concurrent /api/logs/clear) the
clear could rmtree the session dir while the zip was still being built — a real
race on ThreadingHTTPServer — which is why the browser used fetch+Blob to await
the download first. That fetch+Blob download silently failed behind the HTTPS
proxy, so the client now just navigates and the server clears atomically *after*
the zip is in memory. This guards both halves: the response is a valid zip AND
the session dir is gone afterwards.
"""

from __future__ import annotations

import io
import threading
import urllib.request
import zipfile
from http.server import ThreadingHTTPServer

import pytest

from sales_retro_agent import web


@pytest.fixture()
def server(tmp_path, monkeypatch):
    monkeypatch.setattr(web, "SESSION_ROOT", tmp_path / "web_sessions")
    httpd = ThreadingHTTPServer(("127.0.0.1", 0), web.WebRequestHandler)
    port = httpd.server_address[1]
    t = threading.Thread(target=httpd.serve_forever, daemon=True)
    t.start()
    try:
        yield f"http://127.0.0.1:{port}", tmp_path / "web_sessions"
    finally:
        httpd.shutdown()
        httpd.server_close()


def _make_session(session_root):
    session_dir = session_root / "sess-diag"
    session_dir.mkdir(parents=True)
    (session_dir / "events.jsonl").write_text('{"type":"x"}\n', encoding="utf-8")
    (session_dir / "uploaded_audio_transcript.txt").write_text("hello", encoding="utf-8")
    return session_dir


def test_diagnostics_returns_valid_zip_without_clear(server):
    base, session_root = server
    session_dir = _make_session(session_root)
    with urllib.request.urlopen(base + "/api/diagnostics?sessionId=sess-diag", timeout=5) as resp:
        body = resp.read()
    with zipfile.ZipFile(io.BytesIO(body)) as zf:
        names = zf.namelist()
    assert "events.jsonl" in names and "uploaded_audio_transcript.txt" in names
    assert session_dir.exists()  # no clearAfter => session preserved


def test_diagnostics_clear_after_returns_zip_then_removes_session(server):
    base, session_root = server
    session_dir = _make_session(session_root)
    with urllib.request.urlopen(
        base + "/api/diagnostics?sessionId=sess-diag&clearAfter=1", timeout=5
    ) as resp:
        body = resp.read()
    # The download is intact (zip built in memory before the clear) ...
    with zipfile.ZipFile(io.BytesIO(body)) as zf:
        assert "events.jsonl" in zf.namelist()
        assert zf.read("uploaded_audio_transcript.txt") == b"hello"
    # ... and the session dir is gone afterwards.
    assert not session_dir.exists()
