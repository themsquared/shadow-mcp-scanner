# shadow-mcp-scanner

> **Read the write-up:** [Finding the MCP Servers Your Platform Team Doesn't Know About](https://webofmike.com/shadow-mcp-servers/)

Find the MCP servers on your network that nobody registered, and classify what they
hand to an anonymous caller. A second script, `scanner/discover_local.py`, reads
documented MCP client config files on a laptop and never prints secrets.

An MCP server is a small HTTP service. Anyone on your team can start one, and the
useful ones get started precisely because they reach something valuable: the
warehouse, the ticket tracker, the build agent, the CRM. Nothing about that shows up
in a service catalog, and the port it listens on tells you nothing. In July 2025
Trend Micro published a census of 492 internet-exposed MCP servers running with no
client authentication at all, collectively exposing 1,402 tools, more than 90% of
them with direct read access to a data source. That is the last public count anyone
has published, which is its own kind of answer.

The current MCP spec (revision 2026-07-28) removed the `initialize` handshake.
Servers now MUST implement `server/discover`. This repo's lab includes a modern-only
server that speaks that revision and nothing earlier. The unmodified scanner from
`main` labeled that host `not MCP`. The scanner in this tree probes `server/discover`
first so that result is no longer a false negative.

This repo is a scanner and a seven-endpoint network to point it at. It answers three
questions about any `host:port` you give it:

1. **Does this speak MCP?** POST a JSON-RPC `server/discover` with the
   `MCP-Protocol-Version` and `Mcp-Method` headers required by Streamable HTTP in
   2026-07-28. A server that replies with `supportedVersions` (and optional
   `serverInfo` in `_meta`) is an MCP server, whatever the port number suggests.
   Fall back to `initialize` for handshake-era servers (2025-11-25 and earlier).
   Then probe `GET /sse` for the deprecated HTTP+SSE transport.
2. **Does it want credentials?** A 401 means yes. No 401 means the endpoint is open
   to anything that can route to it.
3. **If it is open, what does it give up?** `tools/list` returns the name,
   description, and input schema of everything the server can do.

The scanner is **read-only**. It calls `server/discover` or `initialize`, then
`tools/list`, and never `tools/call`. It is standard library Python with no
dependencies.

## Value proposition

Discovery is the control you are missing. Authorization policy, egress rules, and a
registry are all worth building, and none of them apply to a server you do not know
exists. This finds the servers first, and tells you which ones are already answering
strangers.

## Quickstart

Requires Docker with Compose v2, or Python 3.11+ for the local process lab. Takes
about 15 seconds when the `python:3.12-slim` image is already pulled.

```bash
git clone https://github.com/themsquared/shadow-mcp-scanner
cd shadow-mcp-scanner
bash scripts/demo.sh
```

The scanner is not told which hosts are MCP servers. It is handed seven addresses and
works it out. This table is the after-fix run recorded in
`results/after-fix-scan.txt`:

```
ENDPOINT                           POSTURE                    PROTOCOL     TRANSPORT            TOOLS
------------------------------------------------------------------------------------------------------------
http://legacy-sse:8080/sse         OPEN                       sse-legacy   http+sse (legacy)    1
http://mcp-dual:8080/mcp           OPEN                       dual         streamable-http      1
http://mcp-modern:8080/mcp         OPEN                       modern       streamable-http      1
http://mcp-open:8080/mcp           OPEN                       legacy       streamable-http      3
http://mcp-bearer:8080/mcp         PROTECTED (no discovery)   legacy       streamable-http      -
http://mcp-oauth:8080/mcp          PROTECTED (RFC 9728)       legacy       streamable-http      -
http://build-dashboard:8080        not MCP                    not-mcp      -                    -

  http://legacy-sse:8080/sse answered tools/list with no credentials:
    - read_file: Read a file from the build agent's workspace.

  http://mcp-dual:8080/mcp answered tools/list with no credentials:
    - read_runbook: Read an operations runbook from the internal wiki.

  http://mcp-modern:8080/mcp answered tools/list with no credentials:
    - list_object_store: List buckets in the object store (objects.internal, role mcp-reader).

  http://mcp-open:8080/mcp answered tools/list with no credentials:
    - run_query: Execute a read-only SQL statement against the analytics warehouse (warehous...
    - send_email: Send an email as noreply@ from the shared notifications mailbox.
    - list_customers: Return customer records from the CRM, including billing contact and plan.

scanned 7 endpoints: 6 speak MCP, 4 open with no auth
```

The `PROTOCOL` column is `modern`, `legacy`, `dual`, `sse-legacy`, or `not-mcp`.
`modern` means the host answered `server/discover` and rejected `initialize`.
`dual` means it answered both. `legacy` means `initialize` only. `sse-legacy` is
the deprecated `GET /sse` transport. A 401 before any handshake is listed as
`legacy` because no `DiscoverResult` was observed.

Then check the classification against what the repo claims:

```bash
bash scripts/verify.sh
```

That run recorded in `results/verify.sh.txt` was **33/33 pass**. Tear down with
`docker compose down -v` or `bash scripts/lab_local.sh down`.

## The four postures

| Posture | What the scanner saw | What it means |
|---|---|---|
| `OPEN` | `server/discover` or `initialize` returned 200 with no credentials | Anyone who can route to it can enumerate and call its tools |
| `PROTECTED (no discovery)` | 401 with a bare `WWW-Authenticate: Bearer` | Credentials required, but the client is not told where to get them. **This is out of spec** |
| `PROTECTED (RFC 9728)` | 401 carrying `resource_metadata=` | Correct: the 401 names the metadata document, which names the authorization server |
| `not MCP` | No protocol response on either transport | An ordinary service. Included to show the scanner does not just flag open ports |

The distinction in the middle two rows is not a style preference. The MCP
authorization spec (2025-06-18 and 2026-07-28) says servers **MUST** implement
RFC 9728 and **MUST** use `WWW-Authenticate` on a 401 to indicate the resource
metadata URL. A server that 401s with a bare `Bearer` is protected but
non-conforming, and a client that finds it cannot discover how to authenticate.

Side by side, from `scripts/demo.sh`:

```
mcp-bearer: HTTP 401
  WWW-Authenticate: Bearer
mcp-oauth: HTTP 401
  WWW-Authenticate: Bearer resource_metadata="http://mcp-oauth:8080/.well-known/oauth-protected-resource"
```

## Scanning something real

```bash
# a single host
python3 scanner/scan.py mcp.internal:8080

# a port range on one host
python3 scanner/scan.py buildbox.internal:8000-8100

# a list, as JSON, for a pipeline
python3 scanner/scan.py a.internal:8080,b.internal:3000 --json

# inventory servers you DO hold a credential for
python3 scanner/scan.py mcp.internal:8080 --token "$MCP_TOKEN"

# fail a CI job if anything is open
python3 scanner/scan.py mcp.internal:8080 --fail-on-open
```

`--fail-on-open` exits 1 when any endpoint answered `server/discover` or
`initialize` without credentials, and 0 otherwise. That is the form worth putting
in a pipeline: the scan is cheap enough to run on every deploy, and the failure
is unambiguous.

Only scan networks you are responsible for.

## Laptop config sweep

`scanner/discover_local.py` walks documented MCP client paths and repo files. It
parses JSON (`mcpServers`, `servers`, `projects.*.mcpServers`) and TOML
(`mcp_servers`). Output columns are client, scope, file, server name, transport,
and a URL host or command basename.

It does not print `env` or `headers` values. It does not print a full URL with a
query string. It does not write files. It does not make network calls.

```bash
python3 scanner/discover_local.py
python3 scanner/discover_local.py --home "$HOME" --root /path/to/repo --json
python3 scanner/test_discover_local.py
```

`--home` and `--root` exist so a test can point at fixture files. The fixture
test in `scanner/test_discover_local.py` plants fake tokens in `env`, `headers`,
and URL query strings and checks that none of them appear in the output. That
test recorded in `results/discover_local-test.txt` was **2/2 pass**.

Paths come from the client docs cited in the expansion brief. For Windsurf, only
the current documented path is walked (`~/.config/devin/mcp_config.json` or
`%APPDATA%\devin\mcp_config.json`). The official VS Code page names user-profile
`mcp.json` but not the OS path; the script also looks in the standard VS Code
User folders.

## Architecture

```
                    docker network: shadow-mcp-corp
                    (or scripts/lab_local.sh on 127.0.0.10-16)
  ┌──────────┐
  │ scanner  │──POST server/discover──▶ mcp-modern:8080    MODE=modern      200 + tools
  │          │──POST initialize───────▶ mcp-dual:8080      MODE=dual        both eras
  │ scan.py  │──POST initialize───────▶ mcp-open:8080      MODE=open        200 + tools
  │          │──POST initialize───────▶ mcp-bearer:8080    MODE=bearer      401 bare
  │          │──POST initialize───────▶ mcp-oauth:8080     MODE=oauth       401 + RFC 9728
  │          │──GET  /sse ────────────▶ legacy-sse:8080    MODE=legacy-sse  200 event: endpoint
  └──────────┘──POST server/discover──▶ build-dashboard:8080 MODE=decoy     404
```

- `scanner/scan.py`: the network scanner. Threaded, standard library only.
- `scanner/discover_local.py`: the laptop config sweep. Standard library only.
- `targets/server.py`: one process, seven personalities selected by `MODE`. It
  speaks exactly enough of the protocol to be fingerprinted; it is not a real MCP
  implementation.
- `scripts/demo.sh`: bring the network up and scan it.
- `scripts/lab_local.sh`: same targets as local processes when Compose
  inter-container networking is not usable.
- `scripts/verify.sh`: 33 assertions over the table, the JSON, and the
  discovery fixture test.

The tool names on `mcp-open` (`run_query`, `send_email`, `list_customers`) are
deliberately mundane and deliberately far-reaching, because that combination is the
actual finding. `run_query`'s description leaks a warehouse hostname and a service
account name to an anonymous caller, before anyone calls anything.

## What this does not do

- It does not sweep a CIDR. Give it hosts and ports. Feed it from your own inventory,
  your service mesh, or `nmap` output.
- It does not call tools. Enumeration only, on purpose.
- It does not authenticate beyond a static bearer token via `--token`.
- `discover_local.py` does not inspect running processes and does not follow
  vendor-cloud connectors that never write a local file.
- Finding the servers is step one. Putting them behind a gateway that enforces
  authorization on every tool call, and into a registry that records who owns each
  one, is the actual remediation. See
  [agentgateway](https://agentgateway.dev/) and
  [agentregistry](https://github.com/agentregistry-dev/agentregistry).

## Validation

Commands and blocks of output in this README match files under `results/` from the
run that shipped this change:

- Unmodified `scan.py` from `main`, pointed at the seven-endpoint lab:
  `results/before-fix-scan.txt`. `http://mcp-modern:8080` is `not MCP`.
- `scan.py` after the `server/discover` probe: `results/after-fix-scan.txt`.
  `mcp-modern` is `OPEN` / `modern`. Legacy hosts keep their previous postures.
- `bash scripts/verify.sh`: 33/33 pass (`results/verify.sh.txt`).
- `python3 scanner/test_discover_local.py`: 2/2 pass
  (`results/discover_local-test.txt`).

Those commands were run against `scripts/lab_local.sh` (each MODE bound to
`127.0.0.10` through `127.0.0.16:8080`, names in `/etc/hosts`). Docker Compose
started the same images in this environment, but container-to-container TCP
timed out, so the recorded scan used the local process lab and the same
`targets/server.py`.

## Topics

`mcp` `ai-agents` `security` `kubernetes` `agentgateway` `shadow-it` `scanner`
`model-context-protocol` `discovery` `oauth`

## License

Apache-2.0. See [LICENSE](LICENSE).
