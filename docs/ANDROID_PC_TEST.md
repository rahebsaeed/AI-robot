# Build and Test the Android App Without the Robot

## 1. Build with Android Studio

1. Install Android Studio.
2. In SDK Manager, install Android SDK Platform 36 and Build Tools 36.0.0.
3. Open the `android-app` directory as the project.
4. Wait for Gradle synchronization to finish.
5. Connect an Android phone with USB debugging enabled, or create an emulator.
6. Select the `app` run configuration and press Run.

To create an installable debug APK, use **Build > Build APK(s)**. The output is:

```text
android-app/app/build/outputs/apk/debug/app-debug.apk
```

Command-line build after configuring `ANDROID_HOME`:

```bash
cd android-app
./gradlew testDebugUnitTest assembleDebug
```

## 2. Start the PC Robot Simulator

From the repository root:

```bash
python3 -m venv .venv-mock
source .venv-mock/bin/activate
pip install -r tools/requirements-mock.txt
python3 tools/mock_robot_server.py --token test-token
```

The simulator prints the PC addresses that can be entered in the app.

If the phone and PC use the same Wi-Fi, obtain the address manually with:

```bash
hostname -I
```

Use the first Wi-Fi/LAN address, not `127.0.0.1`.

## 3. Android Connection Values

```text
Robot IP:     PC Wi-Fi address, for example 192.168.1.25
Port:         8765
Pairing token: test-token
Speech locale: en-US
```

For an Android emulator, use `10.0.2.2` instead of the PC IP when the emulator
uses Android Studio's default virtual network.

## 4. Firewall

If the phone cannot connect, allow TCP port 8765 on the PC:

```bash
sudo ufw allow 8765/tcp
```

Do not expose this development server to the Internet.

## 5. Test Scenarios

1. Connect, open **Map**, and confirm that the simulated laboratory map appears.
2. Confirm the robot marker starts near the lower-left area.
3. Open **Conversation**, send `Hello robot`, and check the chat reply.
4. Send `Search for the bottle` and observe waypoint progress and pose movement.
5. Press **Cancel search** during movement.
6. Open **Control**, adjust the speed slider, then hold each manual direction
   and verify the marker moves or rotates.
7. Release the direction and verify movement stops.
8. Hold a direction and close Wi-Fi; the simulator's dead-man timeout stops it.
9. Press **STOP ALL** during a search.
10. Leave **Auto-send speech** enabled, tap the microphone, and speak a command.
    The recognized command is sent when Android returns the final transcript.
    Confirm the app activity changes from `sending` to `queued`, and the server
    prints a `[MOCK COMMAND]` line with the same text and request ID.
11. Disable **Auto-send speech**, speak again, edit the transcript, and tap Send
    to test the review-before-send flow. Speech recognition runs on the phone;
    the simulator receives text and does not process audio.

### Huawei phones without Google services

If Android does not expose a system speech recognition service, the app switches
to its bundled US-English Vosk model. Keep the locale set to `en-US`. The first
microphone press can take several seconds while the 68 MB model is copied into
the app's private storage; later starts are immediate and work offline.

The connected screen has three bottom tabs. **Map** shows the map, last known
robot position and task status. **Conversation** is a chat view with persistent
voice and text input. **Control** contains the speed slider, hold-to-drive
buttons and voice parameters. The red **STOP** action remains fixed in the top
bar on all three tabs.

The simulator does not modify or import the real robot implementation. It only
implements the documented WebSocket protocol on the PC.
