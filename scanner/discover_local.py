#!/usr/bin/env python3
"""Read-only sweep of documented MCP client config paths.

Walks the client and repo paths cited in the 2026-07-28 expansion brief
(section 5). Prints client, scope, file, server name, transport, and a
URL host or command basename.

Never prints `env` or `headers` values. Never prints a full URL with a
query string. Never writes a file. Never makes a network call.
Standard library only.
"""
from __future__ import annotations

import argparse
import json
import os
import sys
import tomllib
from pathlib import Path
from urllib.parse import urlsplit

SKIP_DIRS = {
    ".git",
    "node_modules",
    "__pycache__",
    ".venv",
    "venv",
    "dist",
    "build",
    ".tox",
    ".mypy_cache",
}

# Repo-relative files listed in the brief or on the cited client pages.
REPO_FILES = (
    (".mcp.json", "claude-code", "project"),
    (".cursor/mcp.json", "cursor", "project"),
    (".vscode/mcp.json", "vscode", "project"),
    (".gemini/settings.json", "gemini-cli", "project"),
    (".codex/config.toml", "codex", "project"),
    (".devcontainer/devcontainer.json", "vscode", "devcontainer"),
    (".devcontainer.json", "vscode", "devcontainer"),
)


def _home_files(home: Path) -> list[tuple[Path, str, str]]:
    """Return (path, client, scope) for documented per-user config files."""
    xdg = Path(os.environ.get("XDG_CONFIG_HOME") or (home / ".config"))
    appdata = Path(os.environ.get("APPDATA") or (home / "AppData" / "Roaming"))
    program_files = Path(os.environ.get("PROGRAMFILES") or r"C:\Program Files")
    out: list[tuple[Path, str, str]] = [
        (home / ".claude.json", "claude-code", "user"),
        (home / "Library" / "Application Support" / "Claude" / "claude_desktop_config.json", "claude-desktop", "user"),
        (appdata / "Claude" / "claude_desktop_config.json", "claude-desktop", "user"),
        (home / ".cursor" / "mcp.json", "cursor", "user"),
        (xdg / "devin" / "mcp_config.json", "windsurf", "user"),
        (appdata / "devin" / "mcp_config.json", "windsurf", "user"),
        (home / ".gemini" / "settings.json", "gemini-cli", "user"),
        (home / ".codex" / "config.toml", "codex", "user"),
        (home / ".copilot" / "mcp-config.json", "github-copilot", "user"),
        # Official VS Code page names user-profile mcp.json, not the OS path.
        # These are the standard VS Code User folders plus per-profile copies.
        (xdg / "Code" / "User" / "mcp.json", "vscode", "user"),
        (home / "Library" / "Application Support" / "Code" / "User" / "mcp.json", "vscode", "user"),
        (appdata / "Code" / "User" / "mcp.json", "vscode", "user"),
        (Path("/Library/Application Support/ClaudeCode/managed-mcp.json"), "claude-code", "managed"),
        (Path("/etc/claude-code/managed-mcp.json"), "claude-code", "managed"),
        (program_files / "ClaudeCode" / "managed-mcp.json", "claude-code", "managed"),
    ]
    for profile_root in (
        xdg / "Code" / "User" / "profiles",
        home / "Library" / "Application Support" / "Code" / "User" / "profiles",
        appdata / "Code" / "User" / "profiles",
    ):
        if profile_root.is_dir():
            for mcp in sorted(profile_root.glob("*/mcp.json")):
                out.append((mcp, "vscode", "user-profile"))
    return out


def _load(path: Path):
    raw = path.read_bytes()
    if path.suffix.lower() == ".toml":
        return tomllib.loads(raw.decode())
    text = raw.decode()
    if text.lstrip().startswith("//") or "\n//" in text:
        # Dev Container files sometimes start with comments. Drop full-line
        # comments only; this is not a JSON5 parser.
        lines = [ln for ln in text.splitlines() if not ln.lstrip().startswith("//")]
        text = "\n".join(lines)
    return json.loads(text)


def _url_host(url: str) -> str:
    parts = urlsplit(url)
    host = parts.hostname or ""
    if parts.port:
        return f"{host}:{parts.port}"
    return host


def _transport_and_target(entry: dict) -> tuple[str, str]:
    if not isinstance(entry, dict):
        return "unknown", ""
    declared = (entry.get("type") or "").strip().lower()
    if declared in {"sse", "http", "streamable-http", "ws"}:
        transport = {"streamable-http": "http", "ws": "websocket"}.get(declared, declared)
    elif entry.get("command"):
        transport = "stdio"
    elif entry.get("url") or entry.get("serverUrl"):
        transport = "http"
    else:
        transport = "unknown"
    url = entry.get("url") or entry.get("serverUrl") or ""
    if url:
        return transport, _url_host(str(url))
    command = entry.get("command")
    if command:
        return transport, Path(str(command)).name
    return transport, ""


def _emit_map(rows: list[dict], mapping, client: str, scope: str, path: Path, key_name: str):
    if not isinstance(mapping, dict):
        return
    for name, entry in mapping.items():
        transport, target = _transport_and_target(entry if isinstance(entry, dict) else {})
        rows.append(
            {
                "client": client,
                "scope": scope,
                "file": str(path),
                "name": str(name),
                "transport": transport,
                "target": target,
                "key": key_name,
            }
        )


def extract_servers(data, client: str, default_scope: str, path: Path) -> list[dict]:
    """Pull mcpServers, servers, projects.*.mcpServers, and mcp_servers."""
    rows: list[dict] = []
    if not isinstance(data, dict):
        return rows

    if "mcpServers" in data:
        scope = default_scope
        if path.name == ".claude.json" and default_scope == "user":
            scope = "user"
        _emit_map(rows, data.get("mcpServers"), client, scope, path, "mcpServers")

    if "servers" in data:
        _emit_map(rows, data.get("servers"), client, default_scope, path, "servers")

    projects = data.get("projects")
    if isinstance(projects, dict):
        for _proj, body in projects.items():
            if isinstance(body, dict) and "mcpServers" in body:
                _emit_map(rows, body.get("mcpServers"), client, "local", path, "projects.*.mcpServers")

    if "mcp_servers" in data:
        _emit_map(rows, data.get("mcp_servers"), client, default_scope, path, "mcp_servers")

    custom = data.get("customizations")
    if isinstance(custom, dict):
        vscode = custom.get("vscode")
        if isinstance(vscode, dict):
            mcp = vscode.get("mcp")
            if isinstance(mcp, dict):
                _emit_map(rows, mcp.get("servers"), client, default_scope, path, "customizations.vscode.mcp")
                _emit_map(rows, mcp.get("mcpServers"), client, default_scope, path, "customizations.vscode.mcp")
    return rows


def _iter_repo_files(root: Path):
    root = root.resolve()
    if not root.is_dir():
        return
    for rel, client, scope in REPO_FILES:
        candidate = root / rel
        if candidate.is_file():
            yield candidate, client, scope
    for dirpath, dirnames, filenames in os.walk(root):
        dirnames[:] = [d for d in dirnames if d not in SKIP_DIRS]
        rel_dir = Path(dirpath).relative_to(root)
        depth = len(rel_dir.parts) if rel_dir.parts != (".",) else 0
        if depth > 6:
            dirnames[:] = []
            continue
        for rel, client, scope in REPO_FILES:
            parts = Path(rel).parts
            if len(parts) == 1 and parts[0] in filenames:
                yield Path(dirpath) / parts[0], client, scope
            elif len(parts) == 2 and parts[0] in dirnames:
                nested = Path(dirpath) / parts[0] / parts[1]
                if nested.is_file():
                    yield nested, client, scope


def discover(home: Path, roots: list[Path]) -> list[dict]:
    seen_files: set[Path] = set()
    rows: list[dict] = []

    for path, client, scope in _home_files(home):
        resolved = path
        if not resolved.is_file():
            continue
        try:
            resolved = resolved.resolve()
        except OSError:
            continue
        if resolved in seen_files:
            continue
        seen_files.add(resolved)
        try:
            data = _load(resolved)
        except (OSError, json.JSONDecodeError, tomllib.TOMLDecodeError, UnicodeDecodeError):
            continue
        rows.extend(extract_servers(data, client, scope, resolved))

    for root in roots:
        for path, client, scope in _iter_repo_files(root):
            try:
                resolved = path.resolve()
            except OSError:
                continue
            if resolved in seen_files:
                continue
            seen_files.add(resolved)
            try:
                data = _load(resolved)
            except (OSError, json.JSONDecodeError, tomllib.TOMLDecodeError, UnicodeDecodeError):
                continue
            rows.extend(extract_servers(data, client, scope, resolved))
    return rows


def render(rows: list[dict]) -> None:
    print(f"{'CLIENT':<16} {'SCOPE':<14} {'FILE':<42} {'NAME':<18} {'TRANSPORT':<12} TARGET")
    print("-" * 120)
    for row in rows:
        print(
            f"{row['client']:<16} {row['scope']:<14} {row['file']:<42} "
            f"{row['name']:<18} {row['transport']:<12} {row['target']}"
        )
    print()
    print(f"found {len(rows)} server entries in {len({r['file'] for r in rows})} files")


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(
        description="Read documented MCP client config files. Never prints secrets."
    )
    ap.add_argument(
        "--home",
        default=None,
        help="home directory to walk for user-scope files (default: the real home)",
    )
    ap.add_argument(
        "--root",
        action="append",
        default=[],
        help="repository root to walk for project files; repeatable (default: cwd)",
    )
    ap.add_argument("--json", dest="as_json", action="store_true")
    args = ap.parse_args(argv)

    home = Path(args.home).expanduser() if args.home else Path.home()
    roots = [Path(r).expanduser() for r in args.root] if args.root else [Path.cwd()]
    rows = discover(home, roots)
    if args.as_json:
        print(json.dumps({"servers": rows}, indent=2))
    else:
        render(rows)
    return 0


if __name__ == "__main__":
    sys.exit(main())
