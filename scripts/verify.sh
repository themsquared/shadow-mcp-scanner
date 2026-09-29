#!/usr/bin/env bash
# Assert the classification is what the README claims. Exits non-zero on any miss.
set -uo pipefail
cd "$(dirname "$0")/.."

TARGETS=mcp-open:8080,mcp-bearer:8080,mcp-oauth:8080,legacy-sse:8080,build-dashboard:8080,mcp-modern:8080,mcp-dual:8080
TMP=$(mktemp -d); trap 'rm -rf "$TMP"' EXIT

bash scripts/scan_lab.sh        $TARGETS > "$TMP/out.txt" 2>&1
bash scripts/scan_lab.sh --json $TARGETS > "$TMP/out.json" 2>&1

PASS=0; FAIL=0
ok()   { echo "  PASS  $1"; PASS=$((PASS+1)); }
bad()  { echo "  FAIL  $1"; FAIL=$((FAIL+1)); }
grep_out() { grep -qF -- "$2" "$TMP/out.txt" && ok "$1" || bad "$1 (missing: $2)"; }
# q <description> <expected> <python expression over `d`>
q() {
  local got
  got=$(python3 -c "
import json,sys
d=json.load(open('$TMP/out.json'))
F=lambda h:[x for x in d['findings'] if h in x['endpoint']][0]
print($3)" 2>&1)
  [ "$got" = "$2" ] && ok "$1" || bad "$1 (expected '$2', got '$got')"
}

echo "classification"
q "mcp-open classified OPEN"                    "OPEN"                     "F('mcp-open')['posture']"
q "mcp-bearer classified PROTECTED no discovery" "PROTECTED (no discovery)" "F('mcp-bearer')['posture']"
q "mcp-oauth classified PROTECTED RFC 9728"      "PROTECTED (RFC 9728)"     "F('mcp-oauth')['posture']"
q "legacy-sse found on the legacy transport"     "http+sse (legacy)"        "F('legacy-sse')['transport']"
q "build-dashboard is not flagged as MCP"        "not MCP"                  "F('build-dashboard')['posture']"
q "mcp-modern classified OPEN"                   "OPEN"                     "F('mcp-modern')['posture']"
q "mcp-dual classified OPEN"                     "OPEN"                     "F('mcp-dual')['posture']"

echo "protocol era"
q "mcp-modern protocol is modern"    "modern"     "F('mcp-modern')['protocol']"
q "mcp-dual protocol is dual"        "dual"       "F('mcp-dual')['protocol']"
q "mcp-open protocol is legacy"      "legacy"     "F('mcp-open')['protocol']"
q "legacy-sse protocol is sse-legacy" "sse-legacy" "F('legacy-sse')['protocol']"
q "build-dashboard protocol is not-mcp" "not-mcp" "F('build-dashboard')['protocol']"

echo "disclosure without credentials"
q "mcp-open leaked 3 tools"        "3" "len(F('mcp-open')['tools'])"
q "  run_query"                    "run_query"      "F('mcp-open')['tools'][0]['name']"
q "  send_email"                   "send_email"     "F('mcp-open')['tools'][1]['name']"
q "  list_customers"               "list_customers" "F('mcp-open')['tools'][2]['name']"
q "warehouse hostname leaked in a tool description" "True" \
  "'warehouse-prod.internal:5439' in F('mcp-open')['tools'][0]['description']"
q "legacy server leaked read_file" "read_file" "F('legacy-sse')['tools'][0]['name']"
q "open server disclosed its protocol version" "2025-06-18" "F('mcp-open')['protocol_version']"
q "modern server leaked list_object_store" "list_object_store" "F('mcp-modern')['tools'][0]['name']"
q "dual server leaked read_runbook" "read_runbook" "F('mcp-dual')['tools'][0]['name']"
q "modern server advertised 2026-07-28" "True" \
  "'2026-07-28' in (F('mcp-modern')['protocol_version'] or '')"

echo "protected servers disclose nothing"
q "no tools from mcp-bearer" "0" "len(F('mcp-bearer')['tools'])"
q "no tools from mcp-oauth"  "0" "len(F('mcp-oauth')['tools'])"

echo "RFC 9728 discovery"
q "oauth 401 advertises resource_metadata" "True" \
  "'resource_metadata=' in F('mcp-oauth')['auth_hint']"
q "bearer 401 advertises nothing usable" "True" \
  "'resource_metadata=' not in F('mcp-bearer')['auth_hint']"
q "authorization server resolved from metadata" "True" \
  "any('idp.internal' in n for n in F('mcp-oauth')['notes'])"

echo "report"
grep_out "summary counts 6 MCP, 4 open" "scanned 7 endpoints: 6 speak MCP, 4 open with no auth"
grep_out "table includes a PROTOCOL column" "PROTOCOL"

echo "CI gate"
if bash scripts/scan_lab.sh --fail-on-open mcp-open:8080 >/dev/null 2>&1
then bad "--fail-on-open should exit 1 on an open server"; else ok "--fail-on-open exits 1 on an open server"; fi
if bash scripts/scan_lab.sh --fail-on-open mcp-oauth:8080 >/dev/null 2>&1
then ok "--fail-on-open exits 0 when nothing is open"; else bad "--fail-on-open should exit 0 here"; fi
if bash scripts/scan_lab.sh --fail-on-open mcp-modern:8080 >/dev/null 2>&1
then bad "--fail-on-open should exit 1 on a modern open server"; else ok "--fail-on-open exits 1 on a modern open server"; fi

echo "local discovery"
if python3 scanner/test_discover_local.py >"$TMP/discover-test.txt" 2>&1
then ok "discover_local fixture tests"; else bad "discover_local fixture tests"; cat "$TMP/discover-test.txt"; fi

echo
echo "$((PASS+FAIL)) assertions: $PASS pass, $FAIL fail"
[ "$FAIL" -eq 0 ]
