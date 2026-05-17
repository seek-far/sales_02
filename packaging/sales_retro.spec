# PyInstaller spec for the local one-click build (one-folder / onedir).
#
# Build (must run on the TARGET OS — PyInstaller does not cross-compile):
#     cd packaging
#     pyinstaller sales_retro.spec --noconfirm
# Output: packaging/dist/SalesRetro/  (ship this folder; Inno Setup wraps it)
#
# Notes:
# - one-folder (onedir), not one-file: faster start, far fewer AV false
#   positives.
# - web_static is shipped as data so the server can serve the UI.
# - sounddevice is explicitly excluded: §6 step 1 decoupled the thin backend
#   from PortAudio. Excluding it asserts the decoupling and keeps PortAudio
#   out of the bundle.

import os

from PyInstaller.utils.hooks import collect_submodules

SPECPATH = os.path.dirname(os.path.abspath(SPEC))  # noqa: F821 (SPEC injected)
SRC = os.path.join(SPECPATH, "..", "src")
WEB_STATIC = os.path.join(SRC, "sales_retro_agent", "web_static")

hiddenimports = collect_submodules("openai")

a = Analysis(
    [os.path.join(SPECPATH, "launcher.py")],
    pathex=[SRC],
    binaries=[],
    datas=[(WEB_STATIC, os.path.join("sales_retro_agent", "web_static"))],
    hiddenimports=hiddenimports,
    hookspath=[],
    runtime_hooks=[],
    excludes=["sounddevice", "tkinter", "matplotlib", "pytest"],
    noarchive=False,
)

pyz = PYZ(a.pure)

exe = EXE(
    pyz,
    a.scripts,
    [],
    exclude_binaries=True,
    name="SalesRetro",
    console=True,  # keep a console so users see the local URL / errors
    disable_windowed_traceback=False,
)

coll = COLLECT(
    exe,
    a.binaries,
    a.datas,
    name="SalesRetro",
)
