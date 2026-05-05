# Mobile Apps

This Capacitor app is a native iOS/Android shell for the existing bridge-served
mobile client. It does not fork the browser UI; it connects to a local bridge
URL and then opens the bridge's `/mobile` page.

## Local Setup

```bash
cd apps/mobile
npm install
npm run check
```

Add native projects when the local machine has the platform tooling installed:

```bash
npm run add:android
npm run add:ios
npm run sync
```

Open the native projects:

```bash
npm run open:android
npm run open:ios
```

Build Android from this repo when Android SDK is installed:

```bash
export JAVA_HOME=/path/to/jdk-21
export ANDROID_HOME=/path/to/android/sdk
npm run build:android:debug
```

This app currently targets Android SDK 35 through Capacitor 7.6.2.

Build iOS from this repo on macOS with Xcode and CocoaPods installed:

```bash
cd apps/mobile
npm run sync
npm run open:ios
```

## Bridge URL

Run the bridge on the computer:

```bash
python run_mobile_computer_use_bridge.py --host 0.0.0.0 --workspace /path/to/allowed/root
```

Enter the printed LAN URL in the app, for example:

```text
http://192.168.1.25:45731
```

The app checks `/health` and opens `/mobile` when the bridge is reachable.

## Speech-To-Text

The bridge-served chat page includes a `Voice` button beside `Send`.

- In browsers that expose `SpeechRecognition` / `webkitSpeechRecognition`, the
  page uses the browser implementation.
- In the native iOS/Android app, the page falls back to
  `@capacitor-community/speech-recognition` when Capacitor exposes the plugin to
  the WebView.
- iOS declares `NSMicrophoneUsageDescription` and
  `NSSpeechRecognitionUsageDescription`.
- Android declares `android.permission.RECORD_AUDIO`.

The app asks for microphone/speech access only after the user taps `Voice`.
If neither browser nor native speech recognition is available, the button stays
clickable and reports that speech-to-text is unavailable in that browser or
WebView.

## Native Network Notes

The initial shell allows HTTP navigation so local LAN bridge URLs work. Android
sets `usesCleartextTraffic`, and iOS includes local-network usage text plus ATS
allowances for the local bridge. Keep this limited to trusted networks and the
bridge's local pairing flow. Before store submission, tighten platform-specific
network policy around local-network use.
