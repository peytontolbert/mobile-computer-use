# Bridge Release Binaries

The desktop product is distributed as standalone binaries through GitHub
Releases. Each archive includes a friendly launcher app plus the raw bridge CLI.
Users still need Codex, Cursor, or tmux installed separately; these binaries
only package this project's bridge and launcher.

## Release Artifacts

The `Bridge Release` GitHub Actions workflow builds and uploads:

- `mobile-computer-use-bridge-windows-x64.zip`
- `mobile-computer-use-bridge-macos-arm64.tar.gz`
- `mobile-computer-use-bridge-macos-x64.tar.gz`
- `mobile-computer-use-bridge-linux-x64.tar.gz`

Most users should open `mobile-computer-use.exe` on Windows or
`mobile-computer-use` on macOS/Linux. Advanced users can run
`mobile-computer-use-bridge.exe` or `mobile-computer-use-bridge` directly.

## Creating A Release

Use the manual workflow when you want GitHub Actions to build all desktop
platforms and publish the artifacts:

```bash
gh workflow run bridge-release.yml -f version=0.1.0 -f prerelease=false
```

The workflow creates or updates release tag `v0.1.0` for the supplied version.
It also runs the Python test suite and a `--help` smoke test against each
generated executable before publishing.

Tag pushes also publish releases:

```bash
git tag v0.1.0
git push origin v0.1.0
```

## User Install

After downloading and extracting the archive, users open the desktop launcher.
It lets them choose an allowed workspace folder, start or stop the bridge, see
provider readiness, view local/LAN URLs, optionally show the external IP for
manual port-forwarding setups, approve phones, and remove approved devices.

macOS/Linux:

```bash
./mobile-computer-use
```

Windows PowerShell:

```powershell
.\mobile-computer-use.exe
```

The launcher shows the URL to enter in the iPhone app, Android app, or mobile
browser.

CLI usage remains available for developers and automation:

```bash
./mobile-computer-use-bridge --host 0.0.0.0 --workspace /path/to/allowed/root
```

## Signing Notes

The first binary release is unsigned. Windows SmartScreen and macOS Gatekeeper
may warn users because the executable is new and not code-signed. For broader
distribution, the next step is signing:

- Windows: Authenticode certificate and signed `.exe`/installer.
- macOS: Developer ID signing and notarization, then a `.pkg` or `.dmg`.
- Linux: keep the tarball and optionally add `.deb`, `.rpm`, or AppImage later.
