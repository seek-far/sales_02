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
    assert "前台 + 薄后台代理版" in body  # backend.html topbar marker
    assert 'src="./backend.js"' in body


def test_index_redirects_to_backend(server: str) -> None:
    body = _get(server + "/index.html")
    assert "./backend.html" in body
    assert "纯前端本地版" not in body  # old rules-only UI must be gone


def test_default_config_endpoint(server: str) -> None:
    data = json.loads(_get(server + "/api/default-config"))
    assert data["coachEngine"] == "llm"
    assert data["prompt"].strip()
