# Mobile Computer Use

Secure mobile control for local coding agents and tmux terminals.

Current provider support:

- Codex sessions through `codex exec --json` and `codex exec resume`
- Cursor sessions through the official `cursor-agent` CLI
- Existing Codex chat discovery from local Codex history
- tmux pane attach, capture, and input injection

Claude Code is planned as a provider adapter, but is not enabled yet.

For Cursor, install and authenticate the official CLI first:

```bash
curl https://cursor.com/install -fsS | bash
cursor-agent login
cursor-agent status
```

Alternatively, start the bridge with `CURSOR_API_KEY` in its environment.

## Run

```bash
python run_mobile_computer_use_bridge.py --host 0.0.0.0 --workspace /path/to/allowed/root
```

Then open the printed mobile URL from your phone on the same trusted network.

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
