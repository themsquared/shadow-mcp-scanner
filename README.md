# shadow-mcp-scanner

> 📖 **Read the write-up:** [Finding the MCP Servers Your Platform Team Doesn't Know About](https://webofmike.com/shadow-mcp-servers/)

Find the MCP servers on your network that nobody registered, and classify what they
hand to an anonymous caller.

An MCP server is a small HTTP service. Anyone on your team can start one, and the
useful ones get started precisely because they reach something valuable: the
warehouse, the ticket tracker, the build agent, the CRM. Nothing about that shows up
in a service catalog, and the port it listens on tells you nothing. In July 2025
Trend Micro published a census of 492 internet-exposed MCP servers running with no
client authentication at all, collectively exposing 1,402 tools, more than 90% of
them with direct read access to a data source. That is the last public count anyone
has published, which is its own kind of answer.

This repo is a scanner and a five-endpoint network to point it at. It answers three
questions about any `host:port` you give it:

1. **Does this speak MCP?** POST a JSON-RPC `initialize`. A server that replies with
   a result carrying `protocolVersion` and `serverInfo` is an MCP server, whatever
   the port number suggests. It also probes `GET /sse` for the pre-Streamable
   HTTP+SSE transport, which plenty of deployed servers still run.
2. **Does it want credentials?** A 401 means yes. No 401 means the endpoint is open
   to anything that can route to it.
3. **If it is open, what does it give up?** `tools/list` returns the name,
   description, and input schema of everything the server can do.

The scanner is **read-only**. It calls `initialize` and `tools/list` and never
`tools/call`. It is standard library Python with no dependencies.

## Value proposition

Discovery is the control you are missing. Authorization policy, egress rules, and a
registry are all worth building, and none of them apply to a server you do not know
exists. This finds the servers first, and tells you which ones are already answering
strangers.

## Quickstart

Requires Docker with Compose v2. Takes about 15 seconds.

```bash
git clone https://github.com/themsquared/shadow-mcp-scanner
cd shadow-mcp-scanner
bash scripts/demo.sh
```

The scanner is not told which hosts are MCP servers. It is handed five addresses and
works it out:

```
ENDPOINT                           POSTURE                    TRANSPORT            TOOLS
------------------------------------------------------------------------------------------------
http://legacy-sse:8080/sse         OPEN                       http+sse (legacy)    1
http://mcp-open:8080/mcp           OPEN                       streamable-http      3
http://mcp-bearer:8080/mcp         PROTECTED (no discovery)   streamable-http      -
http://mcp-oauth:8080/mcp          PROTECTED (RFC 9728)       streamable-http      -
http://build-dashboard:8080        not MCP                    -                    -

  http://legacy-sse:8080/sse answered tools/list with no credentials:
    - read_file: Read a file from the build agent's workspace.

  http://mcp-open:8080/mcp answered tools/list with no credentials:
    - run_query: Execute a read-only SQL statement against the analytics warehouse (warehous...
    - send_email: Send an email as noreply@ from the shared notifications mailbox.
    - list_customers: Return customer records from the CRM, including billing contact and plan.

scanned 5 endpoints: 4 speak MCP, 2 open with no auth
```

Then check the classification against what the repo claims:

```bash
bash scripts/verify.sh     # 20 assertions
```

Tear down with `docker compose down -v`.

## The four postures

| Posture | What the scanner saw | What it means |
|---|---|---|
| `OPEN` | `initialize` returned 200 with no credentials | Anyone who can route to it can enumerate and call its tools |
| `PROTECTED (no discovery)` | 401 with a bare `WWW-Authenticate: Bearer` | Credentials required, but the client is not told where to get them. **This is out of spec** |
| `PROTECTED (RFC 9728)` | 401 carrying `resource_metadata=` | Correct: the 401 names the metadata document, which names the authorization server |
| `not MCP` | No protocol response on either transport | An ordinary service. Included to show the scanner does not just flag open ports |

The distinction in the middle two rows is not a style preference. The MCP
authorization spec (revision 2025-06-18) says servers **MUST** implement RFC 9728 and
**MUST** use `WWW-Authenticate` on a 401 to indicate the resource metadata URL. A
server that 401s with a bare `Bearer` is protected but non-conforming, and a client
that finds it cannot discover how to authenticate.

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

`--fail-on-open` exits 1 when any endpoint answered `initialize` without
credentials, and 0 otherwise. That is the form worth putting in a pipeline: the
scan is cheap enough to run on every deploy, and the failure is unambiguous.

Only scan networks you are responsible for.

## Architecture

```
                    docker network: shadow-mcp-corp
  ┌──────────┐
  │ scanner  │──POST initialize──▶ mcp-open:8080        MODE=open       200 + tools
  │          │──POST initialize──▶ mcp-bearer:8080      MODE=bearer     401 bare
  │ scan.py  │──POST initialize──▶ mcp-oauth:8080       MODE=oauth      401 + RFC 9728
  │          │──GET  /sse ───────▶ legacy-sse:8080      MODE=legacy-sse 200 event: endpoint
  └──────────┘──POST initialize──▶ build-dashboard:8080 MODE=decoy      404
```

- `scanner/scan.py` — the scanner. Threaded, standard library only.
- `targets/server.py` — one process, five personalities selected by `MODE`. It
  speaks exactly enough of the protocol to be fingerprinted; it is not a real MCP
  implementation.
- `scripts/demo.sh` — bring the network up and scan it.
- `scripts/verify.sh` — 20 assertions over the table and the JSON.

The tool names on `mcp-open` (`run_query`, `send_email`, `list_customers`) are
deliberately mundane and deliberately far-reaching, because that combination is the
actual finding. `run_query`'s description leaks a warehouse hostname and a service
account name to an anonymous caller, before anyone calls anything.

## What this does not do

- It does not sweep a CIDR. Give it hosts and ports. Feed it from your own inventory,
  your service mesh, or `nmap` output.
- It does not call tools. Enumeration only, on purpose.
- It does not authenticate beyond a static bearer token via `--token`.
- Finding the servers is step one. Putting them behind a gateway that enforces
  authorization on every tool call, and into a registry that records who owns each
  one, is the actual remediation. See
  [agentgateway](https://agentgateway.dev/) and
  [agentregistry](https://github.com/agentregistry-dev/agentregistry).

## Validation

Every command and every block of output in this README was run on the commit that
ships it: 20/20 assertions pass from a fully torn-down stack (`docker compose down
-v`), 2s to bring five containers up and scan them, 1s to verify, on an Apple M4 Max
with the `python:3.12-slim` image already pulled. A first run adds a one-time
216 MB image pull.

## Topics

`mcp` `ai-agents` `security` `kubernetes` `agentgateway` `shadow-it` `scanner`
`model-context-protocol` `discovery` `oauth`

## License

Apache-2.0. See [LICENSE](LICENSE).
