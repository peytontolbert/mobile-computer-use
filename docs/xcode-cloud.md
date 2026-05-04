# Xcode Cloud Setup

Use Xcode Cloud to build the iPhone app without owning a Mac. This still uses
Apple's official signing and TestFlight flow, but the app does not have to be
published on the App Store.

## Development Distribution

Use **TestFlight (Internal Testing Only)** for development builds. Internal
testing makes builds available to App Store Connect users you invite; it does
not list or publish the app publicly.

## App Store Connect

Create a new app record:

- Name: `Computer Use`
- Bundle ID: `com.mobilecomputeruse.app`
- SKU: any unique internal value, for example `mobile-computer-use`
- Platform: iOS

## Xcode Cloud Workflow

Create a workflow for:

- Workspace: `apps/mobile/ios/App/App.xcworkspace`
- Scheme: `App`
- Archive action distribution: `TestFlight (Internal Testing Only)`
- Branch: `main`

The repo includes:

```text
apps/mobile/ios/App/ci_scripts/ci_post_clone.sh
```

Xcode Cloud runs that script after cloning. It installs Node if needed, runs
`npm ci`, validates the app shell, and runs `npx cap sync ios` before Xcode
builds the native project.

## Local iOS Work

On a Mac:

```bash
cd apps/mobile
npm ci
npm run sync
npm run open:ios
```

Then use Xcode to select your team, run on device, or configure Xcode Cloud.
