# Mobile Computer Use

Secure mobile control for local coding agents and tmux terminals.

Use an iPhone app, Android app, or mobile browser to start and continue coding
agent sessions running on your own computer.

## Quick Start

### 1. Install The Bridge

Run this on the computer you want to control:

```bash
python -m pip install git+https://github.com/peytontolbert/mobile-computer-use.git
```

### 2. Install At Least One Agent Provider

Codex:

```bash
npm install -g @openai/codex
codex
codex --version
```

Cursor:

```bash
curl https://cursor.com/install -fsS | bash
cursor-agent login
cursor-agent status
```

You can use Codex, Cursor, or both. Cursor can also use `CURSOR_API_KEY` if that
environment variable is set before starting the bridge.

### 3. Start The Bridge

```bash
mobile-computer-use-bridge --host 0.0.0.0 --workspace /path/to/allowed/root
```

Use a workspace folder you are comfortable letting the mobile device control.
The bridge prints a URL like:

```text
http://192.168.1.25:45731/
```

Keep the terminal open while using the app.

### 4. Connect From Your Phone

You have two options:

- iPhone or Android app: open the app and enter the bridge URL.
- Mobile browser: open the printed URL directly, usually `/mobile` on the same
  host, for example `http://192.168.1.25:45731/mobile`.

When the phone asks for approval, it shows a six-digit code. Look at the bridge
terminal on your computer and type the code shown on your phone.

```text
123456
```

After pairing, choose Codex or Cursor in the mobile UI and start a session.
The chat composer includes a `Voice` button for speech-to-text. In the native
iPhone/Android app it uses the Capacitor speech recognition plugin when the
WebView does not expose browser speech recognition.

## Supported Providers

- Codex sessions through `codex exec --json` and `codex exec resume`
- Cursor sessions through the official `cursor-agent` CLI
- Existing Codex chat discovery from local Codex history
- tmux pane attach, capture, and input injection

Claude Code is planned as a provider adapter, but is not enabled yet.

## Documentation

Structured guides are in [`docs/README.md`](docs/README.md):

- [Architecture](docs/architecture.md) — bridge modes, on-disk state, repository layout  
- [Security and pairing](docs/security-and-pairing.md) — encryption, pairing, origins, networks  
- [Configuration](docs/configuration.md) — CLI reference and environment  
- [HTTP API](docs/http-api.md) — routes, encrypted channel, mobile JSON API  
- [Providers](docs/providers.md) — Codex, Cursor, tmux, future adapters  
- [Testing](docs/testing.md) — running pytest and CI

Release and iOS notes: [Xcode Cloud](docs/xcode-cloud.md), [GitHub Actions iOS](docs/github-actions-ios.md), [mobile release checklist](docs/mobile-release-ux-checklist.md).

## Local Development

For local development from a cloned checkout, this is equivalent:

```bash
python run_mobile_computer_use_bridge.py --host 0.0.0.0 --workspace /path/to/allowed/root
```

Run the automated tests:

```bash
python -m pip install -e ".[dev]"
python -m pytest
```

(Details in [`docs/testing.md`](docs/testing.md).)

## Native Mobile Apps

An optional Capacitor app shell lives in `apps/mobile`. It keeps the browser
client as the source of truth: the app checks a local bridge URL, stores it on
device, and opens the bridge-served `/mobile` page.

```bash
cd apps/mobile
npm install
npm run check
npm run sync
```

Native projects are in `apps/mobile/android` and `apps/mobile/ios`.
The native app declares microphone and speech-recognition permissions so the
chat composer can request dictation access when the user taps `Voice`.

For iPhone builds without a local Mac, use Xcode Cloud and TestFlight internal
testing. See `docs/xcode-cloud.md`.

If Xcode Cloud setup blocks on needing Xcode for the first workflow, use the
manual GitHub Actions macOS workflow in `docs/github-actions-ios.md`.

## Agent Kernel Lite

Agent Kernel Lite imports this package through its compatibility wrapper scripts.
During local development, keep this repository next to `agent_kernel_lite`:

```text
/data/agent_kernel_lite
/data/mobile-computer-use
```

You can override the import path with:

```bash
export MOBILE_COMPUTER_USE_PATH=/path/to/mobile-computer-use
```
