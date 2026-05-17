"""Frozen-app entry point for the local one-click build.

This is the PyInstaller entry script. It is intentionally *not* part of
``src/`` — the runnable program stays decoupled from packaging glue.

Responsibilities:
- Bind only to loopback (no Windows Firewall prompt, local-only access).
- Put session/audio output under a per-user writable dir, because a frozen
  app's CWD may be read-only (e.g. Program Files). ``web.py`` writes
  ``outputs/web_sessions`` relative to CWD, so we chdir there *before*
  starting the server — no change to web.py required.
- Wait until the server actually accepts connections, then open the browser.
"""
from __future__ import annotations

import os
import socket
import sys
import threading
import time
import webbrowser
from pathlib import Path

HOST = "127.0.0.1"
PREFERRED_PORT = 8765
APP_DIR_NAME = "SalesRetro"


def user_data_dir() -> Path:
    """A per-user writable directory for outputs/, stable across runs."""
    if sys.platform == "win32":
        base = os.environ.get("LOCALAPPDATA") or os.path.expanduser("~")
    elif sys.platform == "darwin":
        base = os.path.join(os.path.expanduser("~"), "Library", "Application Support")
    else:
        base = os.environ.get("XDG_DATA_HOME") or os.path.join(
            os.path.expanduser("~"), ".local", "share"
        )
    path = Path(base) / APP_DIR_NAME
    path.mkdir(parents=True, exist_ok=True)
    return path


def pick_port(preferred: int) -> int:
    """Use the preferred port if free, otherwise an OS-assigned free port."""
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as probe:
        probe.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        try:
            probe.bind((HOST, preferred))
            return preferred
        except OSError:
            pass
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as probe:
        probe.bind((HOST, 0))
        return probe.getsockname()[1]


def wait_and_open(port: int, *, timeout: float = 15.0) -> None:
    url = f"http://{HOST}:{port}/backend.html"
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as probe:
            probe.settimeout(0.5)
            if probe.connect_ex((HOST, port)) == 0:
                webbrowser.open(url)
                return
        time.sleep(0.3)
    # Server slow to come up; still surface the URL for the user.
    print(f"Open this in your browser: {url}", flush=True)


def main() -> None:
    data_dir = user_data_dir()
    os.chdir(data_dir)  # web.py writes outputs/web_sessions relative to CWD

    # Import after chdir so module-level paths resolve against the data dir.
    from sales_retro_agent import web

    port = pick_port(PREFERRED_PORT)
    print(f"Sales Retro (local) — data dir: {data_dir}", flush=True)
    threading.Thread(target=wait_and_open, args=(port,), daemon=True).start()
    web.run(HOST, port)  # blocking; handles KeyboardInterrupt internally


if __name__ == "__main__":
    main()
