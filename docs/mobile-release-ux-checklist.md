# Mobile Release UX Checklist

Implemented in the release polish pass:

- Sticky session navigation: Back, session title, Rename, and Stop stay available in the top bar while viewing a session.
- Code-based approval: the phone shows a six-digit code and the desktop bridge requires that exact code.
- Setup guidance: native app help and mobile console setup explain bridge install, Codex, Cursor, browser access, app access, and GitHub source.
- Connection diagnostics: native app connection errors now explain likely network, CORS, LAN, or external-IP causes.
- External access guidance: setup copy mentions external IP/port forwarding as an intentional advanced option.
- Provider readiness: mobile console shows Codex/Cursor provider availability after approval.
- Better session list: sessions show provider, status, model, and short provider session id where available.
- Safer destructive actions: close and interrupt ask for confirmation before taking effect.
- Composer polish: duplicate sends are guarded, Send disables while a non-tmux agent is running, and Stop is available from the sticky nav.
- Reconnect state: the native app announces saved-bridge reconnect attempts and keeps the saved bridge editable.
- Security panel: mobile console shows local-vs-external address guidance and approval duration.

Follow-up candidates:

- Replace browser `confirm()` prompts with styled in-app sheets.
- Add a dedicated revoke-phone button in the security panel.
- Add QR-code pairing for bridge URLs.
- Add HTTPS/relay setup for safer external access.
