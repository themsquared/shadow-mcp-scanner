#!/usr/bin/env python3
"""Small MCP-ish HTTP targets used to exercise the scanner.

One process, five personalities, selected by MODE. Each one is a plausible
posture a real endpoint on your network is in. Nothing here is a real MCP
implementation: it speaks exactly enough Streamable HTTP JSON-RPC to be
fingerprinted, which is the point.

MODE=open        no auth at all, answers initialize and tools/list to anyone
MODE=bearer      401 with a bare WWW-Authenticate, no discovery metadata
MODE=oauth       401 carrying RFC 9728 resource_metadata, serves the document
MODE=legacy-sse  pre-Streamable HTTP+SSE transport, no auth
MODE=decoy       an ordinary web service that is not MCP
"""
import json
import os
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

MODE = os.environ.get("MODE", "open")
NAME = os.environ.get("SERVER_NAME", MODE)
PORT = int(os.environ.get("PORT", "8080"))
TOKEN = os.environ.get("TOKEN", "s3cret-token")
# Advertised externally so the metadata document matches how a scanner reaches it.
PUBLIC = os.environ.get("PUBLIC_URL", f"http://{NAME}:{PORT}")

PROTOCOL_VERSION = "2025-06-18"

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

        # Legacy HTTP+SSE transport: GET /sse opens the stream and announces
        # where to POST. Its presence is a fingerprint all by itself.
        if MODE == "legacy-sse" and self.path in ("/sse", "/"):
            body = b"event: endpoint\ndata: /messages\n\n"
            self._send(200, body, "text/event-stream")
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

        if method == "initialize":
            self._rpc_result(
                req_id,
                {
                    "protocolVersion": PROTOCOL_VERSION,
                    "capabilities": {"tools": {"listChanged": False}},
                    "serverInfo": {"name": NAME, "version": "1.4.2"},
                },
            )
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
    print(f"[{NAME}] mode={MODE} listening on :{PORT}", flush=True)
    ThreadingHTTPServer(("0.0.0.0", PORT), Handler).serve_forever()
