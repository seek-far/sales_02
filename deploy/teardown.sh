#!/usr/bin/env bash
# ============================================================================
# sales-retro — stop the service AND disable boot autostart.
#
# IMPORTANT semantic: this is NOT a pause. After teardown, a system reboot
# will NOT bring sales-retro back — only `bash deploy/setup.sh` will. Use this
# to free RAM/CPU before running another heavy workload on the box (e.g. the
# SDLCMA cloud-rehearsal stack on a 2 GB host).
#
# Does NOT touch Caddy, /opt/sales-retro data, or the unit file — only the
# active/enabled state. Reversible: deploy/setup.sh restores it.
#
# Run on the host as root:  bash deploy/teardown.sh
# Idempotent.
# ============================================================================
set -uo pipefail
UNIT=sales-retro

[ "$(id -u)" = 0 ] || { echo "run as root" >&2; exit 1; }

# --now stops it; disable removes the multi-user.target want → no boot autostart
systemctl disable --now "${UNIT}" 2>/dev/null || true
sleep 1
echo "state: active=$(systemctl is-active ${UNIT} 2>/dev/null) enabled=$(systemctl is-enabled ${UNIT} 2>/dev/null)"
echo "✅ ${UNIT} stopped + disabled. A reboot will NOT start it."
echo "   Bring it back only with: bash deploy/setup.sh"
echo "(Caddy left running; it will 502 on :443 until sales-retro is back.)"
