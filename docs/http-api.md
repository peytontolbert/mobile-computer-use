# HTTP API overview

Base URL: `http://<host>:<port>/` (defaults in [Configuration](configuration.md)).

Request bodies for JSON endpoints are capped (`MAX_JSON_BODY_BYTES`, typically 128 KiB).

## Unauthenticated read

| Method | Path | Description |
| ------ | ---- | ----------- |
| `GET` | `/` or `/mobile` | Mobile web UI (from `web/computer-use-mobile.html` when present). |
| `GET` | `/health` | JSON health: protocol, paired state, bridge JWK, providers, workspaces, sandbox, approval policy. |
| `GET` | `/pair` | Pairing helper page for the HTTPS Agent Kernel app (optional query `app=` for target URL). |

## Pairing (CORS + JSON)

| Method | Path | Description |
| ------ | ---- | ----------- |
| `OPTIONS` | `*` | CORS preflight; origin must be allowed. |
| `POST` | `/pairing/start` | Begin pairing; returns `pairing_id`, bridge JWK, expiry. |
| `POST` | `/pairing/confirm` | Complete pairing with code; returns `grant_id`. |

## Encrypted application channel

| Method | Path | Description |
| ------ | ---- | ----------- |
| `POST` | `/v1/message` | Encrypted envelope; decrypted payload `type` drives behavior (session start/send/status/import/discover, tmux attach, rename, cancel, close, diff read, grant revoke). Legacy type names prefixed with `codex.` remain accepted alongside `computer.*`. |

Exact message types and result shapes are defined in `BridgeState.encrypted_message_response` in `bridge.py`.

## Plaintext revoke

| Method | Path | Description |
| ------ | ---- | ----------- |
| `POST` | `/v1/revoke` | **Disabled** — returns an error; use encrypted `computer.grant.revoke` / `codex.grant.revoke` on `/v1/message` instead. |

## Mobile JSON API (`POST /mobile/api/*`)

These endpoints support the phone-oriented UI and token-based mobile grants:

| Path | Purpose |
| ---- | ------- |
| `/mobile/api/approve` | Console approval using `token`, optional `approval_code`, device fields, `duration`. |
| `/mobile/api/health` | Health plus grant info for an authenticated mobile client. |
| `/mobile/api/sessions` | Active sessions and metadata. |
| `/mobile/api/discover` | External Codex sessions, recent chats, tmux panes. |
| `/mobile/api/import` | Import an external session. |
| `/mobile/api/tmux/attach` | Attach tmux pane. |
| `/mobile/api/start` | Start a session (`provider`, `workspace`, `prompt`, …). |
| `/mobile/api/rename` | Rename session. |
| `/mobile/api/send` | Send follow-up. |
| `/mobile/api/cancel` | Cancel session. |
| `/mobile/api/status` | Session snapshot / stream position. |
| `/mobile/api/close` | Close session. |

Authentication for protected mobile routes uses the mobile grant machinery (`require_mobile_grant`); see code for required body fields.

## Relay mode

When `--relay-url` is set, the same logical paths are exercised through the relay (`handle_relay_request`): `/health`, `/pairing/start`, `/pairing/confirm`, `/v1/message`.
