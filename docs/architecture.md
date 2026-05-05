# Architecture

## Role of the bridge

The bridge is a small HTTP server that runs on your workstation. It:

- Serves a browser UI for **phone / web** control (`web/computer-use-mobile.html` when present, else an embedded fallback).
- Implements **pairing** between a browser origin and the machine, then carries **encrypted** application messages.
- Spawns and talks to **provider CLIs** (Codex, Cursor Agent, Claude Code binary when wired, tmux) and tracks **sessions**.
- Optionally registers with a **relay** so a remote client can reach the same logical API without listening on the LAN.

The protocol identifier exposed in health checks is `agent-kernel-computer-bridge/v1` (with a legacy alias `agent-kernel-codex-bridge/v1` for compatibility).

## Runtime modes

1. **Direct HTTP** (default)  
   `ThreadingHTTPServer` listens on `--host` / `--port`. Clients use pairing + JSON + (after pairing) encrypted envelopes on `/v1/message`.

2. **Relay client**  
   If `--relay-url` is set, the process does not bind a local server for normal use; it connects outbound to the relay API and forwards requests that way. `--relay-public-url` is what phones are told to use when it differs from the internal `--relay-url`.

Development convenience: unless `--no-auto-reload` is passed, the bridge watches its Python entrypoints and **re-execs** itself when sources change.

## Main modules

| Piece | Responsibility |
| ----- | ---------------- |
| `BridgeState` | Config, crypto keys, grants, session index, provider resolution, sandbox/approval policy |
| `Handler` (`BaseHTTPRequestHandler`) | Routing, CORS, JSON/HTML responses, mobile API |
| `run_relay_client` | Outbound relay integration when configured |

## Repository layout (relevant parts)

| Path | Purpose |
| ---- | ------- |
| `mobile_computer_use/bridge.py` | Implementation |
| `run_mobile_computer_use_bridge.py` | Thin entry that calls `main()` |
| `web/computer-use-mobile.html` | Optional mobile page template (token/workspace placeholders) |

## On-disk state (`--config-dir`)

The default config directory is `~/.agent-kernel-lite/codex-bridge`. Typical files:

| File | Purpose |
| ---- | ------- |
| `bridge-device-key.pem` | Long-lived P-256 bridge private key (created if missing) |
| `pairing-grants.json` | Persisted browser pairing grants |
| `mobile-grants.json` | Approved mobile / device grants |
| `computer-use-sessions.json` | Session index for restore across restarts |

Use `--reset-trusted-devices` to clear trust data and exit.

## Workspace model

The bridge accepts one or more `--workspace` roots (default: current working directory if none are passed). Session and file operations are constrained to paths under those roots; see [Security and pairing](security-and-pairing.md).
