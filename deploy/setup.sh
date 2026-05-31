#!/usr/bin/env bash
# ============================================================================
# sales-retro — bring the service UP and make it survive reboot.
# Counterpart: teardown.sh (stops it AND disables boot autostart, so only
# this setup.sh can bring it back — a reboot alone will NOT).
#
# Assumes the one-time deploy in deploy/DEPLOY.md is already done
# (/opt/sales-retro populated, /etc/systemd/system/sales-retro.service
# installed). This script only manages the running/enabled state.
#
# Run on the host as root:  bash deploy/setup.sh
# Idempotent.
# ============================================================================
set -euo pipefail
UNIT=sales-retro

[ "$(id -u)" = 0 ] || { echo "run as root" >&2; exit 1; }

if [ ! -f "/etc/systemd/system/${UNIT}.service" ]; then
  echo "ERROR: /etc/systemd/system/${UNIT}.service missing — do the one-time" >&2
  echo "deploy first (see deploy/DEPLOY.md), then re-run this." >&2
  exit 1
fi

systemctl daemon-reload
systemctl enable --now "${UNIT}"
sleep 1
systemctl is-active "${UNIT}" && echo "✅ ${UNIT} active + enabled (will auto-start on reboot)"
curl -fsS -m6 -o /dev/null http://127.0.0.1:8765/ \
  && echo "✅ backend answering on 127.0.0.1:8765" \
  || echo "⚠️  ${UNIT} active but 127.0.0.1:8765 not answering yet — check 'journalctl -u ${UNIT}'"
echo "(Caddy is independent and untouched; it fronts 8765 on :443.)"
