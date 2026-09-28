#!/usr/bin/env python3
"""Find MCP servers on a network and classify what they expose without credentials.

The scanner asks three questions of every host:port it is given.

  1. Does this speak MCP?   POST JSON-RPC `server/discover` (2026-07-28) first.
     Fall back to `initialize` for handshake-era servers (2025-11-25 and
     earlier). Then probe GET /sse for the deprecated HTTP+SSE transport.
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

LEGACY_PROTOCOL = "2025-06-18"
MODERN_PROTOCOL = "2026-07-28"
META_VERSION = "io.modelcontextprotocol/protocolVersion"
META_CLIENT_INFO = "io.modelcontextprotocol/clientInfo"
META_CLIENT_CAPS = "io.modelcontextprotocol/clientCapabilities"
META_SERVER_INFO = "io.modelcontextprotocol/serverInfo"
UA = "shadow-mcp-scanner/1.0 (+https://github.com/themsquared/shadow-mcp-scanner)"

# Posture values, ordered worst to best. Drives exit codes and the summary.
OPEN = "OPEN"
PROTECTED_NO_DISCOVERY = "PROTECTED (no discovery)"
PROTECTED_RFC9728 = "PROTECTED (RFC 9728)"
NOT_MCP = "not MCP"

# Protocol-era column. Protected endpoints that 401 before a handshake
# cannot be era-classified; those stay legacy because no DiscoverResult
# was observed.
PROTO_MODERN = "modern"
PROTO_LEGACY = "legacy"
PROTO_DUAL = "dual"
PROTO_SSE = "sse-legacy"
PROTO_NOT_MCP = "not-mcp"


def _post_json(url, payload, timeout, token=None, extra_headers=None):
    body = json.dumps(payload).encode()
    req = urllib.request.Request(url, data=body, method="POST")
    req.add_header("Content-Type", "application/json")
    # Streamable HTTP clients advertise both; some servers reject you without it.
    req.add_header("Accept", "application/json, text/event-stream")
    req.add_header("User-Agent", UA)
    if token:
        req.add_header("Authorization", f"Bearer {token}")
    for key, value in (extra_headers or {}).items():
        req.add_header(key, value)
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


def _modern_meta():
    return {
        META_VERSION: MODERN_PROTOCOL,
        META_CLIENT_INFO: {"name": "shadow-mcp-scanner", "version": "1.0"},
        META_CLIENT_CAPS: {},
    }


def _modern_headers(method):
    return {
        "MCP-Protocol-Version": MODERN_PROTOCOL,
        "Mcp-Method": method,
    }


def _parse_json(raw):
    try:
        return json.loads(raw)
    except json.JSONDecodeError:
        return None


def _is_discover_result(result):
    if not isinstance(result, dict):
        return False
    if "supportedVersions" in result:
        return True
    meta = result.get("_meta") or {}
    return isinstance(meta, dict) and META_SERVER_INFO in meta


def _is_initialize_result(result):
    if not isinstance(result, dict):
        return False
    return "protocolVersion" in result or "serverInfo" in result


def _apply_401(finding, headers, path, base):
    finding["transport"] = "streamable-http"
    www = headers.get("WWW-Authenticate", "")
    finding["auth_hint"] = www or "(401, no WWW-Authenticate)"
    if "resource_metadata=" in www:
        finding["posture"] = PROTECTED_RFC9728
    else:
        finding["posture"] = PROTECTED_NO_DISCOVERY
    finding["endpoint"] = base + path
    # No DiscoverResult observed, so the era column stays legacy.
    if finding.get("protocol") == PROTO_NOT_MCP:
        finding["protocol"] = PROTO_LEGACY
    return finding


def _fetch_resource_metadata(finding, headers, timeout):
    www = headers.get("WWW-Authenticate", "")
    if "resource_metadata=" not in www:
        return
    meta_url = www.split('resource_metadata="', 1)[1].split('"', 1)[0]
    try:
        _, _, mraw = _get(meta_url, timeout)
        meta = json.loads(mraw)
        finding["notes"].append(
            "authorization_servers=" + ",".join(meta.get("authorization_servers", []))
        )
    except Exception:
        finding["notes"].append("resource_metadata advertised but not fetchable")


def probe(host, port, timeout=3.0, token=None):
    """Return a finding dict for one host:port."""
    base = f"http://{host}:{port}"
    finding = {
        "endpoint": base,
        "posture": NOT_MCP,
        "protocol": PROTO_NOT_MCP,
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

    discover = {
        "jsonrpc": "2.0",
        "id": "discover-1",
        "method": "server/discover",
        "params": {"_meta": _modern_meta()},
    }
    init = {
        "jsonrpc": "2.0",
        "id": 1,
        "method": "initialize",
        "params": {
            "protocolVersion": LEGACY_PROTOCOL,
            "capabilities": {},
            "clientInfo": {"name": "shadow-mcp-scanner", "version": "1.0"},
        },
    }

    for path in ("/mcp", "/"):
        url = base + path
        try:
            status, headers, raw = _post_json(
                url, discover, timeout, token, extra_headers=_modern_headers("server/discover")
            )
        except (urllib.error.URLError, socket.timeout, OSError) as e:
            finding["notes"].append(f"{path} discover: {e.__class__.__name__}")
            status, headers, raw = (None, {}, b"")

        if status == 401:
            _apply_401(finding, headers, path, base)
            _fetch_resource_metadata(finding, headers, timeout)
            return finding

        if status == 200:
            doc = _parse_json(raw) or {}
            result = doc.get("result") or {}
            if _is_discover_result(result):
                finding["transport"] = "streamable-http"
                finding["posture"] = OPEN
                finding["endpoint"] = url
                finding["protocol"] = PROTO_MODERN
                versions = result.get("supportedVersions") or [MODERN_PROTOCOL]
                if isinstance(versions, list):
                    finding["protocol_version"] = ",".join(str(v) for v in versions)
                else:
                    finding["protocol_version"] = str(versions)
                info = (result.get("_meta") or {}).get(META_SERVER_INFO) or {}
                finding["server"] = info.get("name")
                # Dual-era servers still answer initialize.
                try:
                    istatus, _, iraw = _post_json(url, init, timeout, token)
                except (urllib.error.URLError, socket.timeout, OSError):
                    istatus, iraw = (None, b"")
                if istatus == 200:
                    idoc = _parse_json(iraw) or {}
                    if _is_initialize_result(idoc.get("result") or {}):
                        finding["protocol"] = PROTO_DUAL
                finding["tools"] = _list_tools(url, timeout, token, modern=True)
                return finding

        try:
            status, headers, raw = _post_json(url, init, timeout, token)
        except (urllib.error.URLError, socket.timeout, OSError) as e:
            finding["notes"].append(f"{path}: {e.__class__.__name__}")
            continue

        if status == 401:
            _apply_401(finding, headers, path, base)
            _fetch_resource_metadata(finding, headers, timeout)
            return finding

        if status == 200:
            doc = _parse_json(raw) or {}
            result = doc.get("result") or {}
            if _is_initialize_result(result):
                finding["transport"] = "streamable-http"
                finding["posture"] = OPEN
                finding["endpoint"] = url
                finding["protocol"] = PROTO_LEGACY
                finding["protocol_version"] = result.get("protocolVersion")
                finding["server"] = (result.get("serverInfo") or {}).get("name")
                finding["tools"] = _list_tools(url, timeout, token, modern=False)
                return finding

    # Deprecated HTTP+SSE transport. An `endpoint` event is the giveaway.
    for path in ("/sse", "/"):
        try:
            status, headers, raw = _get(base + path, timeout, accept="text/event-stream")
        except (urllib.error.URLError, socket.timeout, OSError):
            continue
        ctype = headers.get("Content-Type", "")
        if status == 200 and "text/event-stream" in ctype and b"event: endpoint" in raw:
            finding["transport"] = "http+sse (legacy)"
            finding["posture"] = OPEN
            finding["protocol"] = PROTO_SSE
            finding["endpoint"] = base + path
            finding["notes"].append("pre-Streamable HTTP transport still exposed")
            # The SSE stream names where to POST; tools live there.
            msg_path = raw.split(b"data:", 1)[1].split(b"\n", 1)[0].strip().decode()
            finding["tools"] = _list_tools(base + msg_path, timeout, token, modern=False)
            return finding

    return finding


def _list_tools(url, timeout, token=None, modern=False):
    """An open server will describe its own capability to an anonymous caller."""
    if modern:
        payload = {"jsonrpc": "2.0", "id": 2, "method": "tools/list", "params": {"_meta": _modern_meta()}}
        extra = _modern_headers("tools/list")
    else:
        payload = {"jsonrpc": "2.0", "id": 2, "method": "tools/list", "params": {}}
        extra = None
    try:
        status, _, raw = _post_json(url, payload, timeout, token, extra_headers=extra)
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
    print(
        f"{'ENDPOINT':<34} {'POSTURE':<26} {'PROTOCOL':<12} {'TRANSPORT':<20} TOOLS"
    )
    print("-" * 108)
    for f in findings:
        tools = str(len(f["tools"])) if f["posture"] == OPEN else "-"
        print(
            f"{f['endpoint']:<34} {f['posture']:<26} "
            f"{(f.get('protocol') or '-'):<12} "
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
