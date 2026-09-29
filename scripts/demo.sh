#!/usr/bin/env bash
# Bring up the network and scan it. Nothing tells the scanner which hosts are MCP.
set -euo pipefail
cd "$(dirname "$0")/.."

TARGETS=mcp-open:8080,mcp-bearer:8080,mcp-oauth:8080,legacy-sse:8080,build-dashboard:8080,mcp-modern:8080,mcp-dual:8080

if docker compose version >/dev/null 2>&1 && docker info >/dev/null 2>&1; then
  echo "==> starting seven endpoints (compose)"
  docker compose up -d --wait 2>&1 | tail -3 || docker compose up -d
  echo
  echo "==> waiting for listeners"
  for i in $(seq 1 30); do
    if docker compose exec -T scanner python -c "
import socket,sys
for h in ['mcp-open','mcp-bearer','mcp-oauth','legacy-sse','build-dashboard','mcp-modern','mcp-dual']:
    s=socket.socket(); s.settimeout(1)
    try: s.connect((h,8080)); s.close()
    except OSError: sys.exit(1)
" 2>/dev/null; then echo "    all seven listening"; break; fi
    sleep 1
  done
  if ! docker compose exec -T scanner python -c "
import socket
s=socket.socket(); s.settimeout(1)
s.connect(('mcp-open',8080))
" >/dev/null 2>&1; then
    echo "    compose containers cannot reach each other; using local lab"
    bash scripts/lab_local.sh up
  fi
else
  echo "==> starting seven endpoints (local lab)"
  bash scripts/lab_local.sh up
fi
echo

echo "==> scanning the corp network for MCP servers"
bash scripts/scan_lab.sh $TARGETS
