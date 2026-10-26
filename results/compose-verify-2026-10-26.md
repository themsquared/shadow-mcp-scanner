# Compose verify.sh run — 2026-10-26

Honest end-to-end run of `scripts/verify.sh` against the `docker-compose.yml`
stack from `main` (`957bab8d4a80`). Not `lab_local.sh`, not host networking.

## Verdict

**PASS** — 33 assertions, **33 pass, 0 fail**. `verify.sh` exit 0.

## Times

| Phase | Wall time | UTC window |
|---|---|---|
| Bring-up (`docker compose up -d --wait` + all 7 listeners accepting TCP from `scanner`) | **3.875 s** | 2026-10-26T15:23:42Z – 15:23:45Z |
| Scan (`bash scripts/verify.sh`) | **2.530 s** | 2026-10-26T15:24:07Z – 15:24:09Z |
| **Total (bring-up + scan)** | **6.405 s** | — |

There is no image `build` in this compose file; bring-up is pull + create + start
+ wait until each target accepts `host:8080` from the `scanner` container.

## Image cache

**Cold** for the stack image `python:3.12-slim`. It was not present before
`docker compose up` and was pulled during bring-up.

A pre-flight two-container Alpine smoke test (unused by this compose file) had
already pulled `alpine:3.20` to check bridge DNS/TCP. That does not warm
`python:3.12-slim`.

## Hardware and software

| Item | Value |
|---|---|
| CPU | Intel(R) Xeon(R) Processor (KVM), family 6 model 207 |
| Cores | 4 (1 socket, 1 thread/core) |
| RAM | 15.6 GiB (no swap) |
| OS | Ubuntu 24.04.5 LTS (Noble), reported by Docker as containerized |
| Kernel | Linux 6.12.94+ x86_64 (`#1 SMP PREEMPT_DYNAMIC Mon Oct 19 22:12:29 UTC 2026`) |
| Hostname | cursor (Cursor cloud agent VM) |
| Docker Engine | 29.1.3 (`29.1.3-0ubuntu3~24.04.2`), API 1.52 |
| containerd | 2.2.1 |
| runc | 1.3.4-0ubuntu1~24.04.1 |
| Compose | v2.40.3+ds1-0ubuntu1~24.04.1 |
| Storage driver | overlay2 |
| iptables | v1.8.10 **legacy** (nftables backend was not usable) |

## Environment notes (not faked)

Docker was not installed on boot. This session installed `docker.io`,
`docker-compose-v2`, `docker-buildx`, and `iptables`, then started `containerd`
and `dockerd` by hand (PID 1 is `tini`; no systemd). `dockerd` logged
`Deleting nftables IPv4 rules` / `IPv6` with `exit status 1`, so alternatives
were switched to `iptables-legacy` / `ip6tables-legacy` before the timed run.

A previous Cursor VM attempt failed because compose containers could not reach
each other by service name. In this VM, after the legacy-iptables dockerd start,
service-name DNS and TCP on `shadow-mcp-corp` worked. `scripts/lab_local.sh` was
never started. No `/etc/hosts` lab entries. No host-network mode.

## Proof the scan used compose, not the local-lab fallback

`scripts/scan_lab.sh` uses `docker compose exec scanner` only when the scanner
container can open `mcp-open:8080`. That probe succeeded. Docker events during
`verify.sh` show `python /scanner/scan.py` (text, JSON, and `--fail-on-open`)
running as `exec` on `smcp-scanner`. No `targets/server.py` host processes.

Compose DNS/TCP from `scanner` after bring-up:

```
mcp-open:8080 -> 172.18.0.6 TCP_OK
mcp-bearer:8080 -> 172.18.0.2 TCP_OK
mcp-oauth:8080 -> 172.18.0.3 TCP_OK
legacy-sse:8080 -> 172.18.0.4 TCP_OK
build-dashboard:8080 -> 172.18.0.8 TCP_OK
mcp-modern:8080 -> 172.18.0.5 TCP_OK
mcp-dual:8080 -> 172.18.0.7 TCP_OK
```

## Raw: `docker compose ps` after bring-up

```
NAME           IMAGE              COMMAND                  SERVICE           CREATED         STATUS                  PORTS
smcp-bearer    python:3.12-slim   "python /app/server.…"   mcp-bearer        2 seconds ago   Up 1 second
smcp-decoy     python:3.12-slim   "python /app/server.…"   build-dashboard   2 seconds ago   Up 1 second
smcp-dual      python:3.12-slim   "python /app/server.…"   mcp-dual          2 seconds ago   Up 1 second
smcp-legacy    python:3.12-slim   "python /app/server.…"   legacy-sse        2 seconds ago   Up 1 second
smcp-modern    python:3.12-slim   "python /app/server.…"   mcp-modern        2 seconds ago   Up 1 second
smcp-oauth     python:3.12-slim   "python /app/server.…"   mcp-oauth         2 seconds ago   Up 1 second
smcp-open      python:3.12-slim   "python /app/server.…"   mcp-open          2 seconds ago   Up 1 second
smcp-scanner   python:3.12-slim   "sleep infinity"         scanner           2 seconds ago   Up Less than a second
```

## Raw: `docker compose up -d --wait` (create/start tail)

Pull of `python:3.12-slim` succeeded for all eight services, then:

```
 Network shadow-mcp-corp  Creating
 Network shadow-mcp-corp  Created
 Container smcp-decoy  Creating
 Container smcp-modern  Creating
 Container smcp-bearer  Creating
 Container smcp-legacy  Creating
 Container smcp-open  Creating
 Container smcp-oauth  Creating
 Container smcp-dual  Creating
 Container smcp-decoy  Created
 Container smcp-dual  Created
 Container smcp-modern  Created
 Container smcp-open  Created
 Container smcp-oauth  Created
 Container smcp-bearer  Created
 Container smcp-legacy  Created
 Container smcp-scanner  Creating
 Container smcp-scanner  Created
 Container smcp-modern  Starting
 Container smcp-open  Starting
 Container smcp-decoy  Starting
 Container smcp-bearer  Starting
 Container smcp-legacy  Starting
 Container smcp-oauth  Starting
 Container smcp-dual  Starting
 Container smcp-bearer  Started
 Container smcp-oauth  Started
 Container smcp-modern  Started
 Container smcp-legacy  Started
 Container smcp-open  Started
 Container smcp-dual  Started
 Container smcp-decoy  Started
 Container smcp-scanner  Starting
 Container smcp-scanner  Started
 Container smcp-scanner  Waiting
 Container smcp-oauth  Waiting
 Container smcp-bearer  Waiting
 Container smcp-legacy  Waiting
 Container smcp-modern  Waiting
 Container smcp-dual  Waiting
 Container smcp-decoy  Waiting
 Container smcp-open  Waiting
 Container smcp-scanner  Healthy
 Container smcp-decoy  Healthy
 Container smcp-bearer  Healthy
 Container smcp-oauth  Healthy
 Container smcp-legacy  Healthy
 Container smcp-modern  Healthy
 Container smcp-open  Healthy
 Container smcp-dual  Healthy
```

Compose `--wait` marks containers Healthy when they have started; this file has
no healthchecks. Listener readiness was confirmed separately with a TCP connect
from `scanner` to each service name on port 8080 (ready on first attempt).

## Raw: `bash scripts/verify.sh`

```
classification
  PASS  mcp-open classified OPEN
  PASS  mcp-bearer classified PROTECTED no discovery
  PASS  mcp-oauth classified PROTECTED RFC 9728
  PASS  legacy-sse found on the legacy transport
  PASS  build-dashboard is not flagged as MCP
  PASS  mcp-modern classified OPEN
  PASS  mcp-dual classified OPEN
protocol era
  PASS  mcp-modern protocol is modern
  PASS  mcp-dual protocol is dual
  PASS  mcp-open protocol is legacy
  PASS  legacy-sse protocol is sse-legacy
  PASS  build-dashboard protocol is not-mcp
disclosure without credentials
  PASS  mcp-open leaked 3 tools
  PASS    run_query
  PASS    send_email
  PASS    list_customers
  PASS  warehouse hostname leaked in a tool description
  PASS  legacy server leaked read_file
  PASS  open server disclosed its protocol version
  PASS  modern server leaked list_object_store
  PASS  dual server leaked read_runbook
  PASS  modern server advertised 2026-07-28
protected servers disclose nothing
  PASS  no tools from mcp-bearer
  PASS  no tools from mcp-oauth
RFC 9728 discovery
  PASS  oauth 401 advertises resource_metadata
  PASS  bearer 401 advertises nothing usable
  PASS  authorization server resolved from metadata
report
  PASS  summary counts 6 MCP, 4 open
  PASS  table includes a PROTOCOL column
CI gate
  PASS  --fail-on-open exits 1 on an open server
  PASS  --fail-on-open exits 0 when nothing is open
  PASS  --fail-on-open exits 1 on a modern open server
local discovery
  PASS  discover_local fixture tests

33 assertions: 33 pass, 0 fail
```
