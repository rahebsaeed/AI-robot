# Robot Companion Android

Native Android controller for the Rosmaster X3 Plus AI companion.

## Requirements

- Android Studio compatible with Android Gradle Plugin 9.2
- JDK 17
- Android SDK 36
- Android phone running API 26 or newer
- Phone and robot connected to the same trusted Wi-Fi network
- Robot Python dependencies installed from `requirements.txt`

## Robot Configuration

Set a pairing token before starting navigation:

```bash
export AI_MOBILE_ENABLED=1
export AI_MOBILE_PORT=8765
export AI_MOBILE_TOKEN='replace-with-a-random-token'
./start_navigation.sh salle_robotique
```

Confirm the listener on the robot:

```bash
ss -ltnp | rg ':8765'
tail -f /tmp/ai_companion.log
```

## Android Build

Open `android-app/` in Android Studio, allow Gradle synchronization, select a
physical device, and run the `app` configuration. Enter the robot Wi-Fi IP,
port `8765`, and the same pairing token.

The app requests microphone permission when speech input is first used. On
Android 17 and later it also requests local-network permission.

## Connected Interface

The connected experience has three bottom tabs: **Map**, **Conversation**, and
**Control**. Conversation uses chat bubbles with fixed voice/text input. Control
contains a persisted `20%` to `100%` speed slider, hold-to-drive buttons and
voice parameters. A global red STOP action stays fixed in the top bar on every
tab.

Manual movement is dead-man controlled: press and hold Forward, Reverse, Turn
left or Turn right, then release to stop. The map supports bounded `1x` to `6x`
pinch zoom, horizontal/vertical drag and recenter. The last valid AMCL position
remains visible while the robot is stopped.

## Speech Recognition

`Offline` uses Android's on-device `SpeechRecognizer`. Disable it to use the
system recognition service when an on-device model is unavailable. With
`Auto-send speech` enabled, the final transcript is sent directly to the robot;
disable it to review or edit the text before tapping Send.

Phones without an Android recognition service, including Huawei devices
without Google Mobile Services, automatically use the bundled Vosk US-English
model. This fallback runs completely offline and requires the speech locale to
be `en-US`. The first use takes a few seconds while the model is prepared.

## Security Boundary

The current project uses `ws://` for an isolated PFE laboratory LAN and permits
cleartext traffic in `network_security_config.xml`. Do not expose port `8765`
to the Internet. A deployment build must use `wss://`, a verified certificate,
and a short-lived pairing credential.

The complete message contract is in `../docs/MOBILE_PROTOCOL.md`.

To test the application on a PC without the physical robot, follow
`../docs/ANDROID_PC_TEST.md` and run `../tools/mock_robot_server.py`.
