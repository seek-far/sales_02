"""Guard: web_static/ must be declared as package data.

web_static/ is the frontend (backend.html / app.js / ...). It is non-.py, so
setuptools drops it from the wheel unless [tool.setuptools.package-data]
declares it. The PyInstaller path bundles it via spec `datas`, but the public
deploy path (DEPLOY.md: `pip install src`) relies purely on this declaration.
Without it the served root 404s — which is exactly the bug this test catches.
"""

from __future__ import annotations

import fnmatch
from pathlib import Path

try:
    import tomllib  # py3.11+
except ModuleNotFoundError:  # py3.10
    tomllib = None

PKG_ROOT = Path(__file__).resolve().parents[1]  # the `src/` install root
PYPROJECT = PKG_ROOT / "pyproject.toml"
WEB_STATIC = PKG_ROOT / "sales_retro_agent" / "web_static"


def _package_data_globs() -> list[str]:
    if tomllib is not None:
        data = tomllib.loads(PYPROJECT.read_text(encoding="utf-8"))
        return list(
            data.get("tool", {})
            .get("setuptools", {})
            .get("package-data", {})
            .get("sales_retro_agent", [])
        )
    # 3.10 fallback: scan the [tool.setuptools.package-data] table textually.
    text = PYPROJECT.read_text(encoding="utf-8")
    marker = "[tool.setuptools.package-data]"
    assert marker in text, f"missing {marker} in pyproject.toml"
    section = text.split(marker, 1)[1].split("\n[", 1)[0]
    line = next(l for l in section.splitlines() if l.strip().startswith("sales_retro_agent"))
    return [p.strip().strip('"').strip("'") for p in line.split("=", 1)[1].strip(" []").split(",") if p.strip()]


def test_web_static_declared_as_package_data():
    globs = _package_data_globs()
    assert globs, "sales_retro_agent has no package-data globs in pyproject.toml"


def test_every_web_static_file_is_covered():
    globs = _package_data_globs()
    files = [p for p in WEB_STATIC.rglob("*") if p.is_file()]
    assert files, "web_static/ has no files — frontend missing from source tree"
    for f in files:
        rel = f.relative_to(WEB_STATIC.parent).as_posix()  # e.g. web_static/app.js
        assert any(fnmatch.fnmatch(rel, g) for g in globs), (
            f"{rel} is not covered by package-data globs {globs}; "
            "it would be dropped from the wheel"
        )
