#!/usr/bin/env bash
# Bring up the network and scan it. Nothing tells the scanner which hosts are MCP.
set -euo pipefail
cd "$(dirname "$0")/.."

echo "==> starting five endpoints"
docker compose up -d --wait 2>&1 | tail -3 || docker compose up -d
echo

echo "==> waiting for listeners"
for i in $(seq 1 30); do
  if docker compose exec -T scanner python -c "
import socket,sys
for h in ['mcp-open','mcp-bearer','mcp-oauth','legacy-sse','build-dashboard']:
    s=socket.socket(); s.settimeout(1)
    try: s.connect((h,8080)); s.close()
    except OSError: sys.exit(1)
" 2>/dev/null; then echo "    all five listening"; break; fi
  sleep 1
done
echo

echo "==> scanning the corp network for MCP servers"
docker compose exec -T scanner python /scanner/scan.py \
  mcp-open:8080,mcp-bearer:8080,mcp-oauth:8080,legacy-sse:8080,build-dashboard:8080
