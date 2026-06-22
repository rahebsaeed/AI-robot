# Full Build Prompt: Robot Companion Android Application

Use the complete prompt below when asking a coding agent to reconstruct, audit,
or extend the Android application. The prompt is deliberately self-contained.

---

## Role

You are a senior Android and robotics integration engineer. Build a production-
structured native Android application named **Robot Companion** for controlling
and monitoring a Rosmaster X3 Plus mobile robot over a local Wi-Fi network.

Deliver working source code, not a UI mock-up. Resolve implementation details,
compile errors, lifecycle issues, permission handling, network failures, and
safety behavior. Do not replace required features with TODOs or pseudocode.

## System Context

The robot runs:

- Ubuntu on NVIDIA Jetson Orin NX.
- ROS Noetic inside a Docker container.
- AMCL localization in the `map` frame.
- `move_base` navigation.
- A saved occupancy map such as `salle_robotique`.
- YOLO-based object detection and map waypoint search.
- A Python AI process outside Docker.
- A physical USB microphone with local Whisper transcription.
- A Python WebSocket server on the Jetson host.

The Android app is an additional input and monitoring channel. The physical
robot microphone must continue to work when the app is absent. Both inputs are
processed by one robot-side command queue. The Android app must never implement
its own interpretation of natural-language robot commands.

## Primary Objectives

1. Capture speech using the Android phone microphone.
2. Prefer Android on-device speech recognition for free, fast transcription.
3. Show partial transcription and an editable final transcript.
4. Send final command text to the robot over a persistent WebSocket.
5. Display robot responses and task progress.
6. Display the ROS occupancy map, live AMCL pose and orientation.
7. Display the current navigation goal and search waypoint.
8. Provide immediate global stop and search cancellation.
9. Provide hold-to-move manual controls with a dead-man timeout.
10. Reconnect after temporary Wi-Fi loss without duplicating commands.

## Required Technology

Use exactly this platform unless a dependency is proven incompatible:

- Kotlin.
- Native Android application, minimum SDK 26.
- Jetpack Compose with Material 3.
- Unidirectional state flow using `ViewModel` and `StateFlow`.
- Kotlin coroutines for jobs and state collection.
- OkHttp WebSocket client.
- Android `SpeechRecognizer`.
- Android DataStore Preferences for endpoint settings.
- `org.json` or a typed JSON serializer, used consistently.
- JUnit tests for pure coordinate/protocol logic.

Use stable dependency versions. Keep dependency versions centralized or managed
through the Compose BOM. Use JDK 17 and an Android Gradle Plugin version that
supports the selected compile SDK.

## Project Structure

Produce at least the following ownership boundaries:

```text
android-app/
  settings.gradle.kts
  build.gradle.kts
  gradle.properties
  app/
    build.gradle.kts
    proguard-rules.pro
    src/main/AndroidManifest.xml
    src/main/java/com/pfe/robotcompanion/
      MainActivity.kt
      RobotViewModel.kt
      data/
        RobotModels.kt
        RobotSettingsRepository.kt
        RobotWebSocketClient.kt
        MapProjection.kt
      speech/
        SpeechRecognizerManager.kt
      ui/
        RobotApp.kt
        RobotMap.kt
        Theme.kt
    src/main/res/
      drawable/
      values/
      xml/network_security_config.xml
    src/test/
```

Do not put WebSocket parsing, speech recognition and Compose rendering in one
class. Do not hold an Activity reference in the ViewModel.

## WebSocket Endpoint

The user enters:

- Robot IPv4 address, for example `192.168.50.196`.
- Port, default `8765`.
- Pairing token.
- Speech locale, default `en-US`.

Connect to:

```text
ws://ROBOT_IP:8765
```

The development build operates on a trusted laboratory LAN. Configure Android
cleartext access explicitly. Clearly isolate this configuration so a production
variant can replace it with `wss://` and certificate validation.

Never embed a Google Cloud key, service-account file, robot token, fixed robot
IP, or private credential in source code.

## Connection State Machine

Implement these explicit states:

```text
DISCONNECTED -> CONNECTING -> CONNECTED
                       |          |
                       v          v
                     ERROR -> RECONNECTING
```

Requirements:

- Opening the app must not connect before persisted settings are loaded.
- A user disconnect disables automatic reconnection.
- Unexpected failure uses bounded exponential backoff: 1, 2, 4, 8, 10 seconds.
- Only one active WebSocket may exist.
- Prevent stale callbacks from an older socket from replacing the current state.
- Use WebSocket ping frames or protocol ping messages.
- On reconnect, perform a new `hello` handshake and request a fresh map/state.
- Do not automatically resend an unacknowledged motion or natural-language
  command after reconnection.

## Handshake

The first outgoing frame must be:

```json
{
  "v": 1,
  "type": "hello",
  "client_id": "android-uuid",
  "token": "user-entered-token",
  "app": "RobotCompanion"
}
```

The robot answers with `hello_ack`. Do not mark the UI connected merely because
the TCP/WebSocket opened; mark it connected only after `hello_ack`.

Expected acknowledgement:

```json
{
  "v": 1,
  "type": "hello_ack",
  "session_id": "uuid",
  "client_id": "android-uuid",
  "robot": "Rosmaster X3 Plus",
  "server_time": 1781787056.52,
  "capabilities": ["command", "stop", "search_cancel", "teleop", "map", "robot_pose", "navigation_goal", "status"]
}
```

Handle authentication close code `4003` as a non-transient configuration error;
show the error and do not reconnect repeatedly until settings change.

## Command Protocol

Every operation receives a fresh UUID `request_id`.

Natural-language command:

```json
{
  "v": 1,
  "type": "command",
  "request_id": "uuid",
  "text": "search for the bottle",
  "locale": "en-US",
  "source": "android"
}
```

Global stop:

```json
{"v":1,"type":"stop","scope":"all","request_id":"uuid"}
```

Search cancellation:

```json
{"v":1,"type":"search_cancel","request_id":"uuid"}
```

Map reload:

```json
{"v":1,"type":"map_request","request_id":"uuid"}
```

Manual movement:

```json
{
  "v": 1,
  "type": "teleop",
  "request_id": "uuid",
  "direction": "forward",
  "speed": 0.28,
  "active": true
}
```

Supported directions are `forward`, `backward`, `left`, `right`, and `stop`.

## Response Correlation

Handle these server frame types:

- `hello_ack`
- `ack`
- `processing`
- `response`
- `status`
- `message`
- `emotion`
- `map`
- `robot_pose`
- `pong`
- `error`

Correlate `ack`, `processing`, `response`, and `error` by `request_id`.
Represent command states as `queued`, `processing`, `completed`, `failed`,
`rejected`, `cancelled`, or `stopped`. Never infer success solely from a socket
write returning true.

Maintain an in-memory activity list capped at 30 entries. Preserve user commands
and robot responses. Avoid adding the same final response twice when both a
generic `message` and a correlated `response` are received.

## Speech-To-Text Requirements

Use Android `SpeechRecognizer`, not Google Cloud STT.

Implement two user modes:

### Offline mode

- Check `SpeechRecognizer.isOnDeviceRecognitionAvailable()` on API 31+.
- Create with `createOnDeviceSpeechRecognizer()` only when available.
- If unavailable, do not silently switch to cloud recognition.
- Show a clear model-unavailable error.
- Allow the user to change the BCP-47 locale.

### Automatic mode

- Prefer the on-device recognizer.
- Fall back to `createSpeechRecognizer()` when on-device is unavailable.
- Inform state/UI whether recognition is on-device or system-provided.

Recognizer configuration:

- `ACTION_RECOGNIZE_SPEECH`
- `LANGUAGE_MODEL_FREE_FORM`
- selected `EXTRA_LANGUAGE`
- `EXTRA_PARTIAL_RESULTS = true`
- maximum three alternatives
- `EXTRA_PREFER_OFFLINE` according to the setting

Lifecycle rules:

- Request `RECORD_AUDIO` at runtime only when speech is first used.
- Call all recognizer APIs on the main thread.
- Prevent concurrent recognition sessions.
- Call `stopListening()` when the user stops.
- Call `cancel()` when leaving an active capture unexpectedly.
- Call `destroy()` when the owner is cleared.
- Handle every documented error, including language unavailable, recognizer
  busy, timeout, network, permissions and no match.
- Show partial text live.
- Put final text in an editable field.
- Do not send partial results to the robot.
- Do not automatically send a final transcript unless an explicit setting is
  introduced; default behavior requires the Send command.

## ROS Map Rendering

The server sends a static occupancy map as base64 PNG:

```json
{
  "v": 1,
  "type": "map",
  "map_id": "hash",
  "name": "salle_robotique",
  "encoding": "png_base64",
  "image_base64": "...",
  "width": 800,
  "height": 800,
  "resolution": 0.05,
  "origin_x": -20.0,
  "origin_y": -20.0,
  "origin_yaw": 0.0,
  "frame": "map"
}
```

Decode the bitmap once per `map_id`. Do not decode base64 inside every Canvas
draw. Retain the original pixel dimensions and fit the full map initially.

Convert world coordinates to map pixels. First translate by map origin, rotate
by negative `origin_yaw`, divide by resolution, and invert the image Y axis:

```text
dx = worldX - originX
dy = worldY - originY
localX = cos(-originYaw) * dx - sin(-originYaw) * dy
localY = sin(-originYaw) * dx + cos(-originYaw) * dy
pixelX = localX / resolution
pixelY = height - 1 - localY / resolution
```

Apply viewport fit, zoom and pan only after projection.

Render:

- Occupancy bitmap.
- Robot marker when localized.
- Heading line using robot yaw.
- Navigation goal as a blue target.
- Current search waypoint as an amber marker.
- A recenter control.

Support pinch zoom between `1x` and `6x`, bounded pan, and recenter. Clamp pan
to the scaled image bounds so a zoom gesture cannot move the entire map off-screen.
Map gestures must not resize the surrounding layout.

Pose frame:

```json
{
  "v": 1,
  "type": "robot_pose",
  "localized": true,
  "frame": "map",
  "x": -1.42,
  "y": 5.36,
  "yaw": 1.57,
  "pose_age": 0.08,
  "navigation_goal": {"x":1.5,"y":5.5,"yaw":0.0}
}
```

If `localized` is false or pose age is excessive, hide or visibly mark the
robot marker as stale. Never invent a default robot location.

## Search Status

Parse and display:

```json
{
  "type": "status",
  "mode": "searching",
  "phase": "navigating",
  "message": "Moving to search point 4/18",
  "target": "bottle",
  "waypoint_index": 4,
  "waypoint_total": 18,
  "searched_count": 3,
  "found": false,
  "current_waypoint": {"x":-1.5,"y":4.5,"yaw":0.0}
}
```

Status rendering must distinguish ready, searching, navigating, manual, found,
not found, cancelled and stopped states without relying on color alone.

## Manual Control Safety

Manual controls are press-and-hold controls, not toggle buttons.

When a direction is pressed:

1. Start a coroutine job.
2. Send `active:true` immediately.
3. Repeat every 200 ms while held.
4. Read the normalized speed from a persisted `20%` to `100%` slider; default
   to `55%`.

When released, cancelled, app backgrounded, connection lost, or screen disposed:

1. Cancel the heartbeat coroutine.
2. Send `direction:"stop", active:false` when possible.
3. Update local state immediately.

The robot has an independent 650 ms watchdog, but the app must still send the
release frame. A WebSocket disconnect must never leave a local teleop job alive.

Keep the global STOP button separate from manual stop. Global STOP must remain
available whenever connected and must not require confirmation.

## UI Requirements

The connected interface has three bottom tabs: Map, Conversation, and Control.
Keep a red global STOP action fixed in the top app bar on every tab.

Use a restrained robotics-control design:

- Light neutral background.
- Green primary state, amber active/search state, red stop/error state, and blue
  navigation goal.
- No decorative gradients, illustrations, or oversized hero content.
- No cards nested inside cards.
- No marketing descriptions or tutorial paragraphs in the app.
- Use Material icons for connection, microphone, send, refresh, movement,
  recenter, cancel, and stop.
- Keep map and control dimensions stable.
- Ensure text and buttons fit narrow phones and landscape tablets.

Required interface regions:

1. Top app bar with robot name and connection state.
2. Connection form while disconnected:
   - Robot IP
   - Port
   - Pairing token with masked text
   - Speech locale
   - Offline speech switch
   - Connect button
3. Map viewport.
4. Compact robot/task status band.
5. Microphone, editable transcript and Send button.
6. Cancel Search and prominent STOP ALL controls.
7. Directional hold-to-move pad with center manual stop.
8. Recent activity showing user and robot messages.

Use content descriptions for icon buttons. Use familiar icons rather than text
inside rounded rectangles when the symbol is sufficient. Use text only for
unambiguous commands such as `Connect`, `Cancel search`, and `STOP ALL`.

## Permissions and Android Versions

Declare:

- `INTERNET`
- `ACCESS_NETWORK_STATE`
- `RECORD_AUDIO`
- `ACCESS_LOCAL_NETWORK` for Android 17/API 37 targeting
- speech recognition service query for Android 11+

Direct TCP on current Android versions does not require Wi-Fi scanning or
location. Do not request location merely to reach a user-entered IP address.
If Wi-Fi discovery is later introduced, separately evaluate
`NEARBY_WIFI_DEVICES` and version-specific behavior.

Request local-network runtime access on Android 17+ before connecting. Handle
denial without crashing and retain the endpoint settings.

## Persistence

Store robot host, port, token, locale and offline-mode choice in DataStore.
Load values asynchronously. Do not log the token. For a production variant,
replace plain preference token storage with Android Keystore-backed encrypted
storage.

## Error Handling

Provide actionable states for:

- Invalid IP or port.
- Connection refused or timeout.
- Authentication failure.
- Robot restart.
- Wi-Fi loss.
- Malformed JSON.
- Unsupported protocol version.
- Oversized or empty command.
- Command queue full.
- Map unavailable or invalid base64.
- AMCL not localized.
- Microphone permission denied.
- On-device model unavailable.
- Speech timeout or no match.

Network parsing must not crash the UI. Reject invalid coordinates and avoid
drawing NaN/infinite values.

## Testing Requirements

Add unit tests for:

- ROS world-to-pixel conversion, including Y inversion.
- Non-zero map origin.
- Non-zero origin yaw.
- JSON parsing of map, pose, status and response.
- Request/response correlation.
- Reconnect delay bounds.
- Teleop heartbeat cancellation.

Add instrumentation or Compose tests for:

- Connection form validation.
- Microphone permission denial.
- Connected/disconnected controls.
- STOP button availability.
- Long status and response text on a narrow viewport.
- Map placeholder and populated map states.

Manually verify on a physical phone:

1. Connect on the same LAN.
2. Receive map within five seconds.
3. Observe robot pose update during movement.
4. Speak, edit, and send `search for the bottle`.
5. Observe waypoint and status changes.
6. Cancel search.
7. Hold each manual direction and release.
8. Disable Wi-Fi while moving and verify robot watchdog stop.
9. Press STOP during AI inference and map search.
10. Verify the robot's physical microphone still works without the app.

## Acceptance Criteria

- Project syncs and compiles without source modifications.
- No hard-coded secret exists.
- The app remains responsive during speech, map decoding and network traffic.
- `hello_ack` is required before connected state.
- One user command creates one `request_id` and one final correlated result.
- The map uses ROS metadata rather than guessed dimensions.
- Pose and orientation are geometrically correct.
- Manual movement stops on release and heartbeat timeout.
- Global STOP bypasses normal command processing.
- Reconnection does not resend movement or duplicate natural-language commands.
- Robot microphone and Android input can coexist.

## Delivery Instructions

Before finishing:

1. Run the Gradle debug build.
2. Run unit tests and lint.
3. Fix all compilation errors.
4. Report the APK output path.
5. Document robot IP, port and token setup.
6. List any test that could not be run and why.
7. Do not claim physical robot verification unless it was actually performed.

The authoritative backend contract is `docs/MOBILE_PROTOCOL.md`. Do not invent
REST endpoints, ROSBridge dependencies, Firebase services, Google Cloud keys,
or direct motor UDP access from Android.

---
