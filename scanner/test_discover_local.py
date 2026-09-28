#!/usr/bin/env python3
"""Fixture tests for discover_local.py, including secret redaction."""
from __future__ import annotations

import io
import json
import sys
import tempfile
import unittest
from contextlib import redirect_stdout
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import discover_local


SECRET = "super-secret-token-DO-NOT-LEAK"
HEADER_SECRET = "Bearer leaked-header-value"
QUERY_SECRET = "https://mcp.example.internal/v1?api_key=should-never-appear"


def _write(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text)


class DiscoverLocalTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.home = Path(self.tmp.name) / "home"
        self.repo = Path(self.tmp.name) / "repo"
        self.home.mkdir()
        self.repo.mkdir()

        _write(
            self.home / ".claude.json",
            json.dumps(
                {
                    "mcpServers": {
                        "user-http": {
                            "type": "http",
                            "url": QUERY_SECRET,
                            "headers": {"Authorization": HEADER_SECRET},
                        }
                    },
                    "projects": {
                        "/Users/demo/work": {
                            "mcpServers": {
                                "local-stdio": {
                                    "command": "/usr/local/bin/company-mcp",
                                    "args": ["--token", SECRET],
                                    "env": {"API_KEY": SECRET},
                                }
                            }
                        }
                    },
                }
            ),
        )
        _write(
            self.home / ".cursor" / "mcp.json",
            json.dumps(
                {
                    "mcpServers": {
                        "cursor-remote": {
                            "url": "https://api.example.com:8443/mcp?token=" + SECRET,
                            "headers": {"X-Api-Key": SECRET},
                        }
                    }
                }
            ),
        )
        _write(
            self.home / ".codex" / "config.toml",
            f"""
[mcp_servers.docs]
command = "/opt/codex/mcp-docs"
args = ["--secret", "{SECRET}"]

[mcp_servers.docs.env]
TOKEN = "{SECRET}"

[mcp_servers.figma]
url = "https://mcp.figma.com/mcp?session={SECRET}"

[mcp_servers.figma.http_headers]
Authorization = "{HEADER_SECRET}"
""",
        )
        _write(
            self.repo / ".mcp.json",
            json.dumps(
                {
                    "mcpServers": {
                        "shared": {
                            "type": "http",
                            "url": "https://mcp.team.example/mcp",
                            "headers": {"Authorization": HEADER_SECRET},
                        }
                    }
                }
            ),
        )
        _write(
            self.repo / ".vscode" / "mcp.json",
            json.dumps(
                {
                    "servers": {
                        "playwright": {
                            "command": "npx",
                            "args": ["-y", "@microsoft/mcp-server-playwright"],
                            "env": {"PLAYWRIGHT_TOKEN": SECRET},
                        }
                    }
                }
            ),
        )
        _write(
            self.repo / ".devcontainer" / "devcontainer.json",
            json.dumps(
                {
                    "image": "mcr.microsoft.com/devcontainers/base:latest",
                    "customizations": {
                        "vscode": {
                            "mcp": {
                                "servers": {
                                    "in-container": {
                                        "command": "uvx",
                                        "env": {"INNER": SECRET},
                                    }
                                }
                            }
                        }
                    },
                }
            ),
        )

    def tearDown(self):
        self.tmp.cleanup()

    def _run(self, as_json=False):
        argv = ["--home", str(self.home), "--root", str(self.repo)]
        if as_json:
            argv.append("--json")
        buf = io.StringIO()
        with redirect_stdout(buf):
            rc = discover_local.main(argv)
        self.assertEqual(rc, 0)
        return buf.getvalue()

    def test_finds_expected_servers(self):
        data = json.loads(self._run(as_json=True))
        names = {(r["client"], r["scope"], r["name"], r["transport"], r["target"]) for r in data["servers"]}
        self.assertIn(("claude-code", "user", "user-http", "http", "mcp.example.internal"), names)
        self.assertIn(("claude-code", "local", "local-stdio", "stdio", "company-mcp"), names)
        self.assertIn(("cursor", "user", "cursor-remote", "http", "api.example.com:8443"), names)
        self.assertIn(("codex", "user", "docs", "stdio", "mcp-docs"), names)
        self.assertIn(("codex", "user", "figma", "http", "mcp.figma.com"), names)
        self.assertIn(("claude-code", "project", "shared", "http", "mcp.team.example"), names)
        self.assertIn(("vscode", "project", "playwright", "stdio", "npx"), names)
        self.assertIn(("vscode", "devcontainer", "in-container", "stdio", "uvx"), names)

    def test_redacts_secrets_and_query_strings(self):
        text = self._run(as_json=False)
        json_text = self._run(as_json=True)
        for blob in (text, json_text):
            self.assertNotIn(SECRET, blob)
            self.assertNotIn(HEADER_SECRET, blob)
            self.assertNotIn("should-never-appear", blob)
            self.assertNotIn("api_key=", blob)
            self.assertNotIn("session=", blob)
            self.assertNotIn("leaked-header-value", blob)
            self.assertNotIn('"env"', blob)
            self.assertNotIn("http_headers", blob)
            self.assertNotIn("Authorization", blob)


if __name__ == "__main__":
    unittest.main()
