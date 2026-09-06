#!/usr/bin/env python3
"""Find MCP servers on a network and classify what they expose without credentials.

The scanner asks three questions of every host:port it is given.

  1. Does this speak MCP?   POST a JSON-RPC `initialize`. A server that answers
     with a result carrying protocolVersion/serverInfo is an MCP server, whatever
     the port or hostname says. Also probes GET /sse for the legacy HTTP+SSE
     transport, which many deployed servers still run.
  2. Does it want credentials?  A 401 means yes. No 401 means the endpoint is
     open to anyone who can route to it.
  3. If it is open, what does it hand over?  `tools/list` returns the names,
     descriptions, and input schemas of everything the server can do.

Standard library only. Read-only: it never calls tools/call.
"""
import argparse
import concurrent.futures
import json
import socket
import sys
import urllib.error
import urllib.request

PROTOCOL_VERSION = "2025-06-18"
UA = "shadow-mcp-scanner/1.0 (+https://github.com/themsquared/shadow-mcp-scanner)"

# Posture values, ordered worst to best. Drives exit codes and the summary.
OPEN = "OPEN"
PROTECTED_NO_DISCOVERY = "PROTECTED (no discovery)"
PROTECTED_RFC9728 = "PROTECTED (RFC 9728)"
NOT_MCP = "not MCP"


def _post_json(url, payload, timeout, token=None):
    body = json.dumps(payload).encode()
    req = urllib.request.Request(url, data=body, method="POST")
    req.add_header("Content-Type", "application/json")
    # Streamable HTTP clients advertise both; some servers reject you without it.
    req.add_header("Accept", "application/json, text/event-stream")
    req.add_header("User-Agent", UA)
    if token:
        req.add_header("Authorization", f"Bearer {token}")
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            return resp.status, dict(resp.headers), resp.read()
    except urllib.error.HTTPError as e:
        return e.code, dict(e.headers), e.read()


def _get(url, timeout, accept=None):
    req = urllib.request.Request(url, method="GET")
    req.add_header("User-Agent", UA)
    if accept:
        req.add_header("Accept", accept)
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            return resp.status, dict(resp.headers), resp.read()
    except urllib.error.HTTPError as e:
        return e.code, dict(e.headers), e.read()


def probe(host, port, timeout=3.0, token=None):
    """Return a finding dict for one host:port."""
    base = f"http://{host}:{port}"
    finding = {
        "endpoint": base,
        "posture": NOT_MCP,
        "transport": None,
        "server": None,
        "protocol_version": None,
        "tools": [],
        "auth_hint": None,
        "notes": [],
    }

    # Cheap reachability check so unbound ports fail fast rather than on timeout.
    try:
        with socket.create_connection((host, port), timeout=timeout):
            pass
    except OSError as e:
        finding["notes"].append(f"unreachable: {e.__class__.__name__}")
        return finding

    init = {
        "jsonrpc": "2.0",
        "id": 1,
        "method": "initialize",
        "params": {
            "protocolVersion": PROTOCOL_VERSION,
            "capabilities": {},
            "clientInfo": {"name": "shadow-mcp-scanner", "version": "1.0"},
        },
    }

    status, headers, raw = (None, {}, b"")
    for path in ("/mcp", "/"):
        try:
            status, headers, raw = _post_json(base + path, init, timeout, token)
        except (urllib.error.URLError, socket.timeout, OSError) as e:
            finding["notes"].append(f"{path}: {e.__class__.__name__}")
            continue

        if status == 401:
            finding["transport"] = "streamable-http"
            www = headers.get("WWW-Authenticate", "")
            finding["auth_hint"] = www or "(401, no WWW-Authenticate)"
            if "resource_metadata=" in www:
                finding["posture"] = PROTECTED_RFC9728
                meta_url = www.split('resource_metadata="', 1)[1].split('"', 1)[0]
                try:
                    _, _, mraw = _get(meta_url, timeout)
                    meta = json.loads(mraw)
                    finding["notes"].append(
                        "authorization_servers="
                        + ",".join(meta.get("authorization_servers", []))
                    )
                except Exception:
                    finding["notes"].append("resource_metadata advertised but not fetchable")
            else:
                finding["posture"] = PROTECTED_NO_DISCOVERY
            finding["endpoint"] = base + path
            return finding

        if status == 200:
            try:
                doc = json.loads(raw)
            except json.JSONDecodeError:
                continue
            result = doc.get("result") or {}
            if "protocolVersion" in result or "serverInfo" in result:
                finding["transport"] = "streamable-http"
                finding["posture"] = OPEN
                finding["endpoint"] = base + path
                finding["protocol_version"] = result.get("protocolVersion")
                finding["server"] = (result.get("serverInfo") or {}).get("name")
                finding["tools"] = _list_tools(base + path, timeout, token)
                return finding

    # Legacy HTTP+SSE transport. An `endpoint` event is the giveaway.
    for path in ("/sse", "/"):
        try:
            status, headers, raw = _get(base + path, timeout, accept="text/event-stream")
        except (urllib.error.URLError, socket.timeout, OSError):
            continue
        ctype = headers.get("Content-Type", "")
        if status == 200 and "text/event-stream" in ctype and b"event: endpoint" in raw:
            finding["transport"] = "http+sse (legacy)"
            finding["posture"] = OPEN
            finding["endpoint"] = base + path
            finding["notes"].append("pre-Streamable HTTP transport still exposed")
            # The SSE stream names where to POST; tools live there.
            msg_path = raw.split(b"data:", 1)[1].split(b"\n", 1)[0].strip().decode()
            finding["tools"] = _list_tools(base + msg_path, timeout, token)
            return finding

    return finding


def _list_tools(url, timeout, token=None):
    """An open server will describe its own capability to an anonymous caller."""
    payload = {"jsonrpc": "2.0", "id": 2, "method": "tools/list", "params": {}}
    try:
        status, _, raw = _post_json(url, payload, timeout, token)
        if status != 200:
            return []
        doc = json.loads(raw)
        return [
            {"name": t.get("name"), "description": (t.get("description") or "").strip()}
            for t in (doc.get("result") or {}).get("tools", [])
        ]
    except Exception:
        return []


def parse_targets(specs):
    """Accept host:port, host:p1-p2, and comma-joined lists."""
    out = []
    for spec in specs:
        for item in spec.split(","):
            item = item.strip()
            if not item:
                continue
            if ":" not in item:
                raise SystemExit(f"target needs a port: {item}")
            host, ports = item.rsplit(":", 1)
            if "-" in ports:
                lo, hi = ports.split("-", 1)
                for p in range(int(lo), int(hi) + 1):
                    out.append((host, p))
            else:
                out.append((host, int(ports)))
    return out


def main():
    ap = argparse.ArgumentParser(description="Find MCP servers and classify their auth posture.")
    ap.add_argument("targets", nargs="+", help="host:port, host:lo-hi, or a comma-joined list")
    ap.add_argument("--timeout", type=float, default=3.0)
    ap.add_argument("--workers", type=int, default=16)
    ap.add_argument("--token", help="bearer token, to inventory servers you DO hold creds for")
    ap.add_argument("--json", dest="as_json", action="store_true", help="emit JSON instead of a table")
    ap.add_argument("--fail-on-open", action="store_true", help="exit 1 if any endpoint is OPEN (for CI)")
    args = ap.parse_args()

    targets = parse_targets(args.targets)
    findings = []
    with concurrent.futures.ThreadPoolExecutor(max_workers=args.workers) as ex:
        futs = {ex.submit(probe, h, p, args.timeout, args.token): (h, p) for h, p in targets}
        for fut in concurrent.futures.as_completed(futs):
            findings.append(fut.result())

    order = {OPEN: 0, PROTECTED_NO_DISCOVERY: 1, PROTECTED_RFC9728: 2, NOT_MCP: 3}
    findings.sort(key=lambda f: (order.get(f["posture"], 9), f["endpoint"]))

    if args.as_json:
        print(json.dumps({"findings": findings}, indent=2))
    else:
        render(findings)

    if args.fail_on_open and any(f["posture"] == OPEN for f in findings):
        return 1
    return 0


def render(findings):
    mcp = [f for f in findings if f["posture"] != NOT_MCP]
    openf = [f for f in findings if f["posture"] == OPEN]

    print()
    print(f"{'ENDPOINT':<34} {'POSTURE':<26} {'TRANSPORT':<20} TOOLS")
    print("-" * 96)
    for f in findings:
        tools = str(len(f["tools"])) if f["posture"] == OPEN else "-"
        print(
            f"{f['endpoint']:<34} {f['posture']:<26} "
            f"{(f['transport'] or '-'):<20} {tools}"
        )

    for f in openf:
        if not f["tools"]:
            continue
        print()
        print(f"  {f['endpoint']} answered tools/list with no credentials:")
        for t in f["tools"]:
            desc = t["description"]
            if len(desc) > 78:
                desc = desc[:75] + "..."
            print(f"    - {t['name']}: {desc}")

    print()
    noun = "endpoint" if len(findings) == 1 else "endpoints"
    verb = "speaks" if len(mcp) == 1 else "speak"
    print(f"scanned {len(findings)} {noun}: {len(mcp)} {verb} MCP, {len(openf)} open with no auth")
    print()


if __name__ == "__main__":
    sys.exit(main())
