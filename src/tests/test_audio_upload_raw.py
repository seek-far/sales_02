"""Regression for the raw-binary audio-upload path.

Field issue: file uploads sent the audio as base64-in-JSON, which inflated the
payload ~33% and built a giant string, so a large recording took minutes to
"save" with no progress — long enough that users re-clicked and launched
concurrent coach-upload runs. The upload now POSTs the raw file bytes with
metadata in headers; the legacy base64 JSON body stays supported.
"""
from __future__ import annotations

import base64 as b64
import json
import threading
import urllib.request
from http.server import ThreadingHTTPServer
from pathlib import Path

import pytest

from sales_retro_agent import web


@pytest.fixture()
def server(tmp_path, monkeypatch):
    # Redirect session storage into tmp so the test writes nothing under cwd.
    monkeypatch.setattr(web, "SESSION_ROOT", tmp_path / "web_sessions")
    httpd = ThreadingHTTPServer(("127.0.0.1", 0), web.WebRequestHandler)
    port = httpd.server_address[1]
    t = threading.Thread(target=httpd.serve_forever, daemon=True)
    t.start()
    try:
        yield f"http://127.0.0.1:{port}"
    finally:
        httpd.shutdown()
        httpd.server_close()


def test_save_audio_bytes_writes_raw_file(tmp_path, monkeypatch) -> None:
    monkeypatch.setattr(web, "SESSION_ROOT", tmp_path / "web_sessions")
    data = b"\x00\x01\x02binary-audio-payload"
    result = web.save_audio_bytes("sid-1", "rec.mp3", data, folder="uploads")
    assert result["ok"] is True
    assert result["bytes"] == len(data)
    saved = Path(result["path"])
    assert saved.read_bytes() == data
    assert saved.parent.name == "uploads"


def test_raw_binary_upload_endpoint_writes_file_and_decodes_name(server: str) -> None:
    body = b"RIFFfake-wav-bytes" * 100
    req = urllib.request.Request(
        server + "/api/audio-upload",
        data=body,
        headers={
            "Content-Type": "audio/mpeg",  # non-JSON => raw-binary branch
            "X-Session-Id": "sess-raw",
            "X-File-Name": "my%20rec.mp3",  # URL-encoded "my rec.mp3"
            "X-File-Type": "audio/mpeg",
        },
    )
    with urllib.request.urlopen(req, timeout=5) as resp:
        result = json.loads(resp.read().decode("utf-8"))

    assert result["ok"] is True
    assert result["bytes"] == len(body)
    saved = Path(result["path"])
    assert saved.read_bytes() == body
    # unquote restored the space; safe_filename then turned it into "_".
    assert saved.name == "my_rec.mp3"


def test_base64_json_upload_still_supported(server: str) -> None:
    raw = b"chunk-bytes-123"
    payload = {
        "sessionId": "sess-b64",
        "fileName": "legacy.webm",
        "mimeType": "audio/webm",
        "audioBase64": b64.b64encode(raw).decode("ascii"),
    }
    data = json.dumps(payload).encode("utf-8")
    req = urllib.request.Request(
        server + "/api/audio-upload",
        data=data,
        headers={"Content-Type": "application/json"},
    )
    with urllib.request.urlopen(req, timeout=5) as resp:
        result = json.loads(resp.read().decode("utf-8"))

    assert result["ok"] is True
    assert result["bytes"] == len(raw)
    assert Path(result["path"]).read_bytes() == raw
