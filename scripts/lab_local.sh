#!/usr/bin/env bash
# Start the lab targets as local processes when Docker inter-container
# networking is unavailable. Each service gets its own 127.0.0.N:8080
# so the scanner still sees the compose hostnames.
set -euo pipefail
cd "$(dirname "$0")/.."

PIDDIR="${SHADOW_MCP_LAB_PIDDIR:-${XDG_RUNTIME_DIR:-/tmp}/shadow-mcp-lab}"
mkdir -p "$PIDDIR"

# name ip mode
SERVICES=(
  "mcp-open 127.0.0.10 open"
  "mcp-bearer 127.0.0.11 bearer"
  "mcp-oauth 127.0.0.12 oauth"
  "legacy-sse 127.0.0.13 legacy-sse"
  "build-dashboard 127.0.0.14 decoy"
  "mcp-modern 127.0.0.15 modern"
  "mcp-dual 127.0.0.16 dual"
)

ensure_host() {
  local ip="$1" name="$2"
  if grep -qE "[[:space:]]${name}([[:space:]]|$)" /etc/hosts; then
    return 0
  fi
  if command -v sudo >/dev/null 2>&1 && sudo -n true 2>/dev/null; then
    printf '%s %s\n' "$ip" "$name" | sudo tee -a /etc/hosts >/dev/null
  else
    echo "missing /etc/hosts entry: $ip $name" >&2
    return 1
  fi
}

alive() {
  local ip="$1"
  python3 -c "
import socket,sys
s=socket.socket(); s.settimeout(0.4)
try:
    s.connect(('$ip', 8080)); s.close()
except OSError:
    sys.exit(1)
"
}

cmd="${1:-up}"
case "$cmd" in
  up)
    for spec in "${SERVICES[@]}"; do
      set -- $spec
      name="$1" ip="$2" mode="$3"
      ensure_host "$ip" "$name"
      if alive "$ip"; then
        continue
      fi
      if [[ -f "$PIDDIR/$name.pid" ]] && kill -0 "$(cat "$PIDDIR/$name.pid")" 2>/dev/null; then
        continue
      fi
      BIND="$ip" PORT=8080 MODE="$mode" SERVER_NAME="$name" PUBLIC_URL="http://$name:8080" \
        python3 targets/server.py >"$PIDDIR/$name.log" 2>&1 &
      echo $! >"$PIDDIR/$name.pid"
    done
    for i in $(seq 1 30); do
      ok=1
      for spec in "${SERVICES[@]}"; do
        set -- $spec
        alive "$2" || { ok=0; break; }
      done
      if [[ "$ok" -eq 1 ]]; then
        echo "local lab listening on 127.0.0.10-16:8080"
        exit 0
      fi
      sleep 0.2
    done
    echo "local lab failed to start" >&2
    exit 1
    ;;
  down)
    for spec in "${SERVICES[@]}"; do
      set -- $spec
      name="$1"
      if [[ -f "$PIDDIR/$name.pid" ]]; then
        kill "$(cat "$PIDDIR/$name.pid")" 2>/dev/null || true
        rm -f "$PIDDIR/$name.pid"
      fi
    done
    echo "local lab stopped"
    ;;
  *)
    echo "usage: $0 up|down" >&2
    exit 2
    ;;
esac
