# App Review Notes

Use this when resubmitting after a rejection for incomplete metadata, placeholder
content, or reviewer access.

## Demo URL

The native app includes a `Try Review Demo` button that opens:

```text
https://peytontolbert.com/mobiledemo/
```

Upload the contents of `web/mobiledemo/` to that hosted path before submitting.
The demo is static and safe: it does not connect to a real computer, does not
use provider API keys, and does not modify a filesystem. It demonstrates the
same mobile flows reviewers need to inspect: provider readiness, session list,
new session, chat messages, stop, close, and voice input availability.

## Suggested App Review notes

```text
No account is required.

Computer Use normally connects to a bridge running on the user's own trusted
computer. For App Review, please use the built-in Try Review Demo button or open:

https://peytontolbert.com/mobiledemo/

Review steps:
1. Open the app.
2. Tap Try Review Demo.
3. Open an existing demo session or tap New Demo Session.
4. Send a message, inspect the session list, use Stop/Back, and try Voice input.

The production workflow is:
- User installs the open-source desktop bridge from GitHub.
- User runs Codex or Cursor locally on their own computer.
- The mobile app connects to that private bridge over a trusted network.
```

## Icon and metadata checklist

- Use `marketing/app-icons/computer-use-app-icon-1024.png` for the App Store
  Connect app icon.
- Confirm the iOS binary includes `AppIcon-512@2x.png` from the same final icon.
- Confirm Android launcher icons use the same final Computer Use mark.
- Screenshots should show the bridge connection screen and the demo/mobile
  session UI, not placeholder Capacitor screens.
- Description should state that this is a companion app for a desktop bridge.
