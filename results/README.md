# Lab scan transcripts

These files are raw output from the commands named in each filename.
They were produced against the seven-endpoint lab in this repository.

`before-fix-scan.txt` and `before-fix-scan.json` were captured with the
unmodified `scanner/scan.py` from `main` (initialize, then `GET /sse`).
`mcp-modern` is the 2026-07-28-only server.

`after-fix-scan.txt` and `after-fix-scan.json` were captured after the
scanner started probing `server/discover` first.

`verify.sh.txt` is the full `scripts/verify.sh` run after that change.
