#!/usr/bin/env bash
# Linux/macOS build — also used as a cross-check that the .spec / launcher are
# correct. NOTE: this produces a Linux/macOS bundle, NOT a Windows one.
# The shippable Windows build must run build.ps1 on Windows.
set -euo pipefail
cd "$(dirname "$0")"

python3 -m venv .buildenv
. .buildenv/bin/activate
pip install --upgrade pip >/dev/null
pip install ../src pyinstaller          # thin backend deps only, no sounddevice
pyinstaller sales_retro.spec --noconfirm --clean

echo
echo "Done. Bundle: packaging/dist/SalesRetro/"
echo "Run ./dist/SalesRetro/SalesRetro then open http://127.0.0.1:8765/backend.html"
