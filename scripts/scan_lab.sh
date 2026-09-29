#!/usr/bin/env bash
# Run scan.py against the lab. Prefer docker compose when containers can
# reach each other; otherwise start the local process lab.
set -euo pipefail
cd "$(dirname "$0")/.."

if docker compose exec -T scanner python -c "
import socket
s=socket.socket(); s.settimeout(1)
s.connect(('mcp-open', 8080))
s.close()
" >/dev/null 2>&1; then
  exec docker compose exec -T scanner python /scanner/scan.py "$@"
fi

bash scripts/lab_local.sh up >/dev/null
exec python3 scanner/scan.py "$@"
