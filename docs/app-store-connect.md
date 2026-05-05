# App Store Connect Setup

Use this once you are ready to create the Mobile Computer Use app record.

## App Record

- Platform: iOS
- Name: Computer Use
- Bundle ID: `com.peytontolbert.mobilecomputeruse`
- SKU: `mobile-computer-use`
- Primary category: Developer Tools
- Secondary category: Productivity
- Sign-in required: No

## Build Paths

You can use either Apple Xcode Cloud or GitHub Actions.

### Xcode Cloud

Use:

```text
apps/mobile/ios/App/App.xcworkspace
```

Scheme:

```text
App
```

The post-clone script is:

```text
apps/mobile/ios/App/ci_scripts/ci_post_clone.sh
```

### GitHub Actions

Use the manual workflow:

```text
.github/workflows/ios-testflight.yml
```

Required repository secrets:

- `APPLE_TEAM_ID`
- `APP_STORE_CONNECT_API_KEY_ID`
- `APP_STORE_CONNECT_API_ISSUER_ID`
- `APP_STORE_CONNECT_API_KEY_P8_BASE64`

Create the App Store Connect API key in:

```text
Users and Access -> Integrations -> App Store Connect API -> Keys
```

The key must have build/app management access for this app.

## Review Preparation

- Upload `web/mobiledemo/` to `https://peytontolbert.com/mobiledemo/`.
- Use the notes in `docs/app-review.md`.
- Use privacy answers from `docs/privacy.md`.
- Submit screenshots that show the real bridge setup screen and review demo.
- Do not submit screenshots of the placeholder Capacitor shell.
