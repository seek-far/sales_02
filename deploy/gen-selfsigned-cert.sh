#!/usr/bin/env bash
# Generate a self-signed cert for a BARE IP with the IP in subjectAltName.
# Modern browsers ignore CN and require the IP in SAN, so this is mandatory.
#
# Usage:  ./gen-selfsigned-cert.sh 203.0.113.45 [out_dir]
# Output: <out_dir>/cert.pem  <out_dir>/key.pem  (default out_dir: ./tls)
#
# Only needed if you want to feed your own cert to Caddy (see the alternative
# block in Caddyfile.selfsigned) or use a non-Caddy proxy. If you're fine with
# `tls internal`, you do NOT need this script.
set -euo pipefail

IP="${1:?usage: gen-selfsigned-cert.sh <public-ip> [out_dir]}"
OUT="${2:-./tls}"
mkdir -p "$OUT"

openssl req -x509 -newkey rsa:2048 -nodes \
  -keyout "$OUT/key.pem" -out "$OUT/cert.pem" \
  -days 825 -subj "/CN=$IP" \
  -addext "subjectAltName=IP:$IP"

echo "Wrote $OUT/cert.pem and $OUT/key.pem (SAN=IP:$IP, 825 days)"
