# Configuration

## CLI reference

All flags are defined on the bridge executable, for example:

```bash
python run_mobile_computer_use_bridge.py --help
# or, after install:
mobile-computer-use-bridge --help
```

| Flag | Default | Description |
| ---- | ------- | ----------- |
| `--host` | `127.0.0.1` | Bind address. Use `0.0.0.0` only on a trusted LAN for phone access. |
| `--port` | `45731` | TCP port. |
| `--workspace` | _(cwd)_ | Allowed workspace root. Repeat for multiple roots. |
| `--config-dir` | `~/.agent-kernel-lite/codex-bridge` | Directory for keys, grants, and session index. |
| `--codex-bin` | `codex` | Codex CLI on `PATH` or explicit path. |
| `--claude-bin` | `claude` | Claude Code CLI (when provider is enabled upstream). |
| `--cursor-bin` | `cursor-agent` | Cursor official CLI. |
| `--tmux-bin` | `tmux` | tmux binary for attach/capture flows. |
| `--timeout` | `900` | Provider operation timeout (seconds). |
| `--sandbox` | `danger-full-access` | Codex sandbox: `danger-full-access`, `read-only`, `workspace-write`. |
| `--approval-policy` | `never` | `never` or `on-request`. |
| `--allow-origin` | _(see code)_ | Extra allowed `Origin` for CORS; repeatable. |
| `--relay-url` | _(empty)_ | If set, run relay client instead of local HTTP server. |
| `--relay-public-url` | same as `--relay-url` | Public base URL advertised to phones. |
| `--relay-ttl` | `86400` | Relay route lifetime (seconds). |
| `--reset-trusted-devices` | off | Clear pairing grants and mobile grants, then exit. |
| `--no-auto-reload` | off | Disable restart-on-source-change during development. |

## Environment

Provider CLIs typically use their own environment (e.g. Cursor’s `CURSOR_API_KEY` when not using `cursor-agent login`). Configure those **before** starting the bridge in the same shell or via your process manager.

## Agent Kernel Lite path

When developing alongside [Agent Kernel Lite](../README.md#agent-kernel-lite), set:

```bash
export MOBILE_COMPUTER_USE_PATH=/path/to/mobile-computer-use
```

if the checkout is not beside the consumer project.
