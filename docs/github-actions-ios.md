# iOS Builds Without A Mac

Xcode Cloud is Apple's official CI service, but Apple still documents Xcode as
the normal entry point for the first workflow. For a no-Mac setup, this repo also
includes a manual GitHub Actions workflow that runs on GitHub's hosted macOS
runners and uploads to App Store Connect/TestFlight.

Workflow:

```text
.github/workflows/ios-testflight.yml
```

It runs only when manually started from GitHub Actions.

## Required GitHub Secrets

Create these in:

```text
GitHub repo -> Settings -> Secrets and variables -> Actions -> New repository secret
```

### `APPLE_TEAM_ID`

Your 10-character Apple Developer Team ID.

Find it in:

```text
developer.apple.com/account -> Membership details
```

### `APP_STORE_CONNECT_API_KEY_ID`

The App Store Connect API key ID.

### `APP_STORE_CONNECT_API_ISSUER_ID`

The issuer ID shown on the App Store Connect API Keys page.

### `APP_STORE_CONNECT_API_KEY_P8_BASE64`

The downloaded `.p8` private key encoded as base64 on your local machine:

```bash
base64 -i AuthKey_XXXXXXXXXX.p8 | pbcopy
```

On Linux:

```bash
base64 -w 0 AuthKey_XXXXXXXXXX.p8
```

Store the one-line base64 output as the secret value.

## Create The API Key

In App Store Connect:

```text
Users and Access -> Integrations -> App Store Connect API -> Keys
```

Create a key with a role that can manage builds for the app. Download the `.p8`
file immediately; Apple only allows downloading it once.

## Run The Build

In GitHub:

```text
Actions -> iOS TestFlight -> Run workflow -> main
```

If signing succeeds, the workflow uploads the archive to App Store Connect.
After Apple finishes processing, the build appears in TestFlight for internal
testing.

## App Store Connect App

Use this bundle ID:

```text
com.peytontolbert.mobilecomputeruse
```

Use TestFlight Internal Testing for development. This does not publish the app on
the App Store.
