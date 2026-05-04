# Providers

## Codex

Codex is the original direct coding-agent provider.

The bridge supports:

- starting a new `codex exec --json` turn
- resuming an imported Codex session id
- discovering recent Codex chats from `~/.codex/history.jsonl` and thread metadata
- reading workspace diffs

## Cursor

Cursor is supported through the official `cursor-agent` CLI.

The bridge supports:

- starting a new `cursor-agent -p --output-format stream-json` turn
- continuing a Cursor session with `cursor-agent --resume`
- using the local Cursor login created by `cursor-agent login`
- using `CURSOR_API_KEY` when that environment variable is set before starting the bridge
- reading workspace diffs

Install and authenticate Cursor before starting the bridge:

```bash
curl https://cursor.com/install -fsS | bash
cursor-agent login
cursor-agent status
```

## tmux

tmux is the attach path for already-running interactive terminals.

The bridge lists panes, detects Codex-like child processes where possible, captures
pane output, and submits mobile input through a temporary tmux buffer plus `C-m`.

## Future adapters

Claude Code should be added as a provider adapter only after its session and
command contracts are validated.
