# Security and pairing

## Cryptography

After pairing, request bodies on `/v1/message` use an **encrypted envelope** (not raw JSON). The design uses:

- **ECDH** on **P-256** between the bridge key and the browser’s public key (JWK).
- **HKDF-SHA256** and **AES-256-GCM** for the message layer.

The bridge loads or creates a private key at `bridge-device-key.pem` under `--config-dir`. Protect that directory like any machine-bound secret material.

## Pairing flow

Pairing ties a **browser origin** (and its public key) to a **grant** stored on disk:

1. **`POST /pairing/start`** — Client sends a browser P-256 JWK. The bridge prints a **six-digit code** and metadata to the terminal and returns `pairing_id` and `expires_at` (on the order of minutes).
2. User enters the code where the app expects it; the workstation **must approve** (console path) where applicable.
3. **`POST /pairing/confirm`** — Client completes with `pairing_id` and code. On success the bridge returns `grant_id` and grant lifetime.

Brute-force on the code is limited (attempt cap per `pairing_id`). Grants expire after an extended TTL (see `PAIRING_GRANT_TTL_SECONDS` in code, typically ~30 days).

## Origin allowlist (CORS)

Cross-origin browser calls are only accepted when the `Origin` header matches an allowed origin. Defaults include production and local dev origins (HTTPS app host, Capacitor/Ionic schemes, localhost); you can add more with **`--allow-origin`** (repeatable).

Private Network Access: preflight may send `Access-Control-Request-Private-Network`; the bridge responds with `Access-Control-Allow-Private-Network: true` when the origin is allowed, so browsers can reach LAN addresses from public pages when policy allows.

## Binding the server

- **`--host 127.0.0.1`** (default): Only local connections. Safest default.
- **`--host 0.0.0.0`** or a LAN IP: Any host on the network that can route to the machine may reach the HTTP port. The bridge prints a warning when listening beyond loopback.

Use LAN binding **only on trusted networks** and only with pairing discipline (short codes, console approval, no untrusted Wi‑Fi).

## Workspace isolation

Operations that touch the filesystem are limited to **`--workspace` roots**. The selected workspace for a session must be **exactly** one of the allowed roots or **inside** it (path-wise). This reduces accidental reads/writes outside intended trees; it is not a substitute for full OS sandboxing unless you also use provider-level sandbox options appropriate to your agents.

## Speech-to-text privacy

Voice input is local to the chat composer flow: the app requests microphone and
speech-recognition access only after the user taps `Voice`, then inserts the
recognized text into the textarea. The text is not sent to the bridge or an
agent until the user taps `Send`.

Browser speech recognition and native OS speech services may process audio
according to the browser or platform provider's speech-recognition behavior.
Users who do not want OS/browser speech services can keep using the keyboard or
the system keyboard's built-in dictation controls.

## Codex sandbox flag

`--sandbox` selects Codex CLI sandbox behavior (`danger-full-access`, `read-only`, `workspace-write`). The default favors environments where stricter sandboxes fail (e.g. some bridge/desktop setups). Tighten when your host supports it.

## Approval policy

`--approval-policy` (`never` or `on-request`) affects Codex-side approval behavior where supported by the invoked CLI.

## Revocation

Clients can revoke a grant via encrypted messages of type `computer.grant.revoke` / `codex.grant.revoke`, or use **`--reset-trusted-devices`** on the server to wipe all trust state.
