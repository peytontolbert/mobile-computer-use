# Privacy

Initial App Store privacy answers for Mobile Computer Use:

- Tracking: No.
- Third-party advertising: No.
- Data used for tracking: No.
- Account required: No.
- Data linked to user: No, for the app binary itself.

The app stores the bridge URL locally on device so the user can reconnect to
their own trusted computer. The bridge-served session UI may display local
workspace/session metadata from the user's computer after explicit pairing.

Permissions:

- Local Network: connects to the bridge running on the user's trusted computer.
- Microphone: used only after the user taps Voice.
- Speech Recognition: converts speech to draft text before Send.

The app does not send speech text to an agent until the user taps Send. Native
speech recognition may use Apple or platform speech services according to the
OS-level speech-recognition behavior.

`PrivacyInfo.xcprivacy` currently declares no tracking, no collected data types,
and no required-reason accessed API categories. Revisit the privacy manifest and
App Store privacy answers if new native plugins are added.
