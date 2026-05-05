# Testing

This repository uses **pytest** for automated checks. Tests focus on stable, hermetic behavior: crypto helpers, CLI parsing, `BridgeState` policy methods, relay-shaped handlers, and HTTP smoke checks—without requiring Codex, Cursor, or tmux binaries for a full integration run.

## Run the suite

From the repository root, install the package with dev dependencies and run pytest:

```bash
python -m pip install -e ".[dev]"
python -m pytest
```

Verbose output:

```bash
python -m pytest -v
```

## Layout

| Path | Role |
| -----| ---- |
| `tests/conftest.py` | Shared fixtures (`isolated_config_dir`, `bridge_namespace`) |
| `tests/test_bridge_crypto.py` | Base64 / JWK / key loading helpers |
| `tests/test_bridge_state.py` | Provider names, CORS rules, health payload, `parse_args`, relay health |
| `tests/test_bridge_http.py` | Live `ThreadingHTTPServer` on an ephemeral port: `GET /health`, basic 404 |

Each test run uses a **temporary config directory** under pytest’s `tmp_path`, so pairing grants and device keys do not touch your real `~/.agent-kernel-lite` tree.

## What is not covered yet

End-to-end flows (encrypted `/v1/message` roundtrips, spawning `codex`/`cursor-agent`/tmux, pairing UI) are intentionally out of scope for the default suite so CI stays fast and dependency-free. Add targeted integration tests behind optional markers or fixtures if you extend automation.

## Continuous integration

The **pytest** GitHub Actions workflow (`.github/workflows/pytest.yml`) runs the same command on push and pull requests.
