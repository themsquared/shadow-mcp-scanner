#!/usr/bin/env python3
"""Small MCP-ish HTTP targets used to exercise the scanner.

One process, several personalities, selected by MODE. Each one is a plausible
posture a real endpoint on your network is in. Nothing here is a real MCP
implementation: it speaks exactly enough of the protocol to be fingerprinted.

MODE=open        no auth, answers initialize and tools/list (2025-06-18)
MODE=bearer      401 with a bare WWW-Authenticate, no discovery metadata
MODE=oauth       401 carrying RFC 9728 resource_metadata, serves the document
MODE=legacy-sse  pre-Streamable HTTP+SSE transport, no auth
MODE=decoy       an ordinary web service that is not MCP
MODE=modern      2026-07-28 only: answers server/discover, rejects initialize
MODE=dual        dual-era: server/discover and initialize on the same endpoint
"""
import json
import os
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

MODE = os.environ.get("MODE", "open")
NAME = os.environ.get("SERVER_NAME", MODE)
PORT = int(os.environ.get("PORT", "8080"))
BIND = os.environ.get("BIND", "0.0.0.0")
TOKEN = os.environ.get("TOKEN", "s3cret-token")
# Advertised externally so the metadata document matches how a scanner reaches it.
PUBLIC = os.environ.get("PUBLIC_URL", f"http://{NAME}:{PORT}")

PROTOCOL_VERSION = "2025-06-18"
MODERN_PROTOCOL = "2026-07-28"
META_VERSION = "io.modelcontextprotocol/protocolVersion"
META_CLIENT_INFO = "io.modelcontextprotocol/clientInfo"
META_CLIENT_CAPS = "io.modelcontextprotocol/clientCapabilities"
META_SERVER_INFO = "io.modelcontextprotocol/serverInfo"

# Deliberately mundane-sounding tools with immodest reach. This is the part that
# matters: an open server hands this list to anyone who asks.
TOOLS = {
    "open": [
        {
            "name": "run_query",
            "description": (
                "Execute a read-only SQL statement against the analytics warehouse "
                "(warehouse-prod.internal:5439, service account svc_analytics)."
            ),
            "inputSchema": {
                "type": "object",
                "properties": {"sql": {"type": "string"}},
                "required": ["sql"],
            },
        },
        {
            "name": "send_email",
            "description": "Send an email as noreply@ from the shared notifications mailbox.",
            "inputSchema": {
                "type": "object",
                "properties": {
                    "to": {"type": "string"},
                    "subject": {"type": "string"},
                    "body": {"type": "string"},
                },
                "required": ["to", "subject", "body"],
            },
        },
        {
            "name": "list_customers",
            "description": "Return customer records from the CRM, including billing contact and plan.",
            "inputSchema": {
                "type": "object",
                "properties": {"limit": {"type": "integer"}},
            },
        },
    ],
    "legacy-sse": [
        {
            "name": "read_file",
            "description": "Read a file from the build agent's workspace.",
            "inputSchema": {
                "type": "object",
                "properties": {"path": {"type": "string"}},
                "required": ["path"],
            },
        }
    ],
    "modern": [
        {
            "name": "list_object_store",
            "description": (
                "List buckets in the object store "
                "(objects.internal, role mcp-reader)."
            ),
            "inputSchema": {
                "type": "object",
                "properties": {"prefix": {"type": "string"}},
            },
        }
    ],
    "dual": [
        {
            "name": "read_runbook",
            "description": "Read an operations runbook from the internal wiki.",
            "inputSchema": {
                "type": "object",
                "properties": {"name": {"type": "string"}},
                "required": ["name"],
            },
        }
    ],
}


class Handler(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"

    def log_message(self, fmt, *args):  # keep compose logs readable
        pass

    # ---- helpers -------------------------------------------------------
    def _send(self, code, body: bytes, ctype="application/json", extra=None):
        self.send_response(code)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(body)))
        for k, v in (extra or {}).items():
            self.send_header(k, v)
        self.end_headers()
        self.wfile.write(body)

    def _json(self, code, obj, extra=None):
        self._send(code, json.dumps(obj).encode(), "application/json", extra)

    def _rpc_result(self, req_id, result):
        self._json(200, {"jsonrpc": "2.0", "id": req_id, "result": result})

    def _rpc_error(self, http_status, req_id, code, message, data=None):
        err = {"code": code, "message": message}
        if data is not None:
            err["data"] = data
        self._json(http_status, {"jsonrpc": "2.0", "id": req_id, "error": err})

    def _authorized(self):
        return self.headers.get("Authorization", "") == f"Bearer {TOKEN}"

    def _unauthorized(self):
        """The difference that decides whether a client can self-serve."""
        if MODE == "oauth":
            # RFC 9728: point the client at the protected resource metadata.
            hdr = f'Bearer resource_metadata="{PUBLIC}/.well-known/oauth-protected-resource"'
        else:
            hdr = "Bearer"
        self._json(
            401,
            {
                "jsonrpc": "2.0",
                "id": None,
                "error": {"code": -32001, "message": "Unauthorized"},
            },
            extra={"WWW-Authenticate": hdr},
        )

    def _supported_versions(self):
        if MODE == "dual":
            return [MODERN_PROTOCOL, PROTOCOL_VERSION]
        return [MODERN_PROTOCOL]

    def _server_info(self):
        return {"name": NAME, "version": "1.4.2"}

    def _header(self, name):
        # RFC 9110 field names are case-insensitive; BaseHTTPRequestHandler
        # already exposes a case-insensitive mapping.
        return self.headers.get(name)

    def _legacy_initialize(self, req_id):
        self._rpc_result(
            req_id,
            {
                "protocolVersion": PROTOCOL_VERSION,
                "capabilities": {"tools": {"listChanged": False}},
                "serverInfo": self._server_info(),
            },
        )

    def _reject_initialize(self, req_id, missing_headers):
        # 2026-07-28 versioning: a modern-only HTTP server rejects a legacy
        # initialize that lacks the required headers with 400 Bad Request.
        # The same page says a modern-only server SHOULD name the versions it
        # supports in any error it returns to initialize.
        data = {"supported": self._supported_versions(), "requested": self._header("MCP-Protocol-Version")}
        if missing_headers:
            self._rpc_error(
                400,
                req_id,
                -32020,
                "Header mismatch: required MCP-Protocol-Version and Mcp-Method headers are missing",
                data,
            )
            return
        self._rpc_error(
            404,
            req_id,
            -32601,
            "Method not found: initialize",
            data,
        )

    def _validate_modern_headers(self, method, req):
        """Return an error response if Streamable HTTP header rules fail.

        From the 2026-07-28 Streamable HTTP page: every POST MUST carry
        MCP-Protocol-Version and Mcp-Method; the header values MUST match
        the body. Missing or mismatched values are 400 + HeaderMismatch
        (-32020). An unsupported version is 400 +
        UnsupportedProtocolVersionError (-32022).
        """
        proto = self._header("MCP-Protocol-Version")
        mcp_method = self._header("Mcp-Method")
        params = req.get("params") or {}
        meta = params.get("_meta") or {}
        body_proto = meta.get(META_VERSION)
        if not proto or not mcp_method:
            self._rpc_error(
                400,
                req.get("id"),
                -32020,
                "Header mismatch: required MCP-Protocol-Version and Mcp-Method headers are missing",
                {"supported": self._supported_versions(), "requested": proto},
            )
            return False
        if proto != body_proto:
            self._rpc_error(
                400,
                req.get("id"),
                -32020,
                "Header mismatch: MCP-Protocol-Version does not match params._meta",
            )
            return False
        if mcp_method != method:
            self._rpc_error(
                400,
                req.get("id"),
                -32020,
                "Header mismatch: Mcp-Method does not match body method",
            )
            return False
        if proto not in self._supported_versions():
            self._rpc_error(
                400,
                req.get("id"),
                -32022,
                "Unsupported protocol version",
                {"supported": self._supported_versions(), "requested": proto},
            )
            return False
        return True

    def _discover_result(self, req_id):
        self._rpc_result(
            req_id,
            {
                "resultType": "complete",
                "supportedVersions": self._supported_versions(),
                "capabilities": {"tools": {}},
                "_meta": {META_SERVER_INFO: self._server_info()},
                "ttlMs": 3600000,
                "cacheScope": "public",
            },
        )

    def _modern_tools_result(self, req_id):
        tools = TOOLS.get(MODE, TOOLS["modern"])
        self._rpc_result(
            req_id,
            {
                "resultType": "complete",
                "tools": tools,
                "ttlMs": 300000,
                "cacheScope": "public",
                "_meta": {META_SERVER_INFO: self._server_info()},
            },
        )

    def _handle_modern(self, req, method, req_id):
        if method == "initialize":
            missing = not (self._header("MCP-Protocol-Version") and self._header("Mcp-Method"))
            self._reject_initialize(req_id, missing)
            return
        if not self._validate_modern_headers(method, req):
            return
        if method == "server/discover":
            self._discover_result(req_id)
        elif method == "tools/list":
            self._modern_tools_result(req_id)
        else:
            # Unknown method: 404 and JSON-RPC -32601 (Streamable HTTP page).
            self._rpc_error(404, req_id, -32601, f"Method not found: {method}")

    # ---- routes --------------------------------------------------------
    def do_GET(self):
        if MODE == "decoy":
            self._send(200, b"<html><body><h1>Build Dashboard</h1></body></html>", "text/html")
            return

        if self.path == "/.well-known/oauth-protected-resource":
            if MODE != "oauth":
                self._send(404, b"not found", "text/plain")
                return
            self._json(
                200,
                {
                    "resource": PUBLIC,
                    "authorization_servers": ["https://idp.internal/realms/agents"],
                    "bearer_methods_supported": ["header"],
                    "scopes_supported": ["mcp:tools:read", "mcp:tools:invoke"],
                },
            )
            return

        # 2026-07-28 Streamable HTTP: a modern-only server answers GET/DELETE
        # to the MCP endpoint with 405 Method Not Allowed.
        if MODE in ("modern", "dual") and self.path in ("/mcp", "/"):
            self._send(405, b"method not allowed", "text/plain")
            return

        # Legacy HTTP+SSE transport: GET /sse opens the stream and announces
        # where to POST. Its presence is a fingerprint all by itself.
        if MODE == "legacy-sse" and self.path in ("/sse", "/"):
            body = b"event: endpoint\ndata: /messages\n\n"
            self._send(200, body, "text/event-stream")
            return

        self._send(404, b"not found", "text/plain")

    def do_DELETE(self):
        if MODE in ("modern", "dual") and self.path in ("/mcp", "/"):
            self._send(405, b"method not allowed", "text/plain")
            return
        self._send(404, b"not found", "text/plain")

    def do_POST(self):
        if MODE == "decoy":
            self._send(404, b"not found", "text/plain")
            return

        # A genuine legacy deployment does NOT speak Streamable HTTP. It only
        # accepts JSON-RPC on the endpoint its SSE stream advertised.
        if MODE == "legacy-sse" and self.path != "/messages":
            self._send(404, b"not found", "text/plain")
            return

        length = int(self.headers.get("Content-Length", "0"))
        raw = self.rfile.read(length) if length else b"{}"
        try:
            req = json.loads(raw or b"{}")
        except json.JSONDecodeError:
            self._json(400, {"error": "bad json"})
            return

        method = req.get("method", "")
        req_id = req.get("id")

        if MODE in ("bearer", "oauth") and not self._authorized():
            self._unauthorized()
            return

        if MODE == "modern":
            self._handle_modern(req, method, req_id)
            return

        if MODE == "dual":
            # Versioning page: an initialize request selects legacy semantics.
            if method == "initialize":
                self._legacy_initialize(req_id)
                return
            if self._header("MCP-Protocol-Version") or self._header("Mcp-Method"):
                self._handle_modern(req, method, req_id)
                return
            if method == "tools/list":
                self._rpc_result(req_id, {"tools": TOOLS.get(MODE, TOOLS["open"])})
                return
            self._rpc_error(200, req_id, -32601, f"Method not found: {method}")
            return

        if method == "initialize":
            self._legacy_initialize(req_id)
        elif method == "tools/list":
            self._rpc_result(req_id, {"tools": TOOLS.get(MODE, TOOLS["open"])})
        elif method == "notifications/initialized":
            self._send(202, b"", "text/plain")
        else:
            self._json(
                200,
                {
                    "jsonrpc": "2.0",
                    "id": req_id,
                    "error": {"code": -32601, "message": f"Method not found: {method}"},
                },
            )


if __name__ == "__main__":
    print(f"[{NAME}] mode={MODE} listening on {BIND}:{PORT}", flush=True)
    ThreadingHTTPServer((BIND, PORT), Handler).serve_forever()
