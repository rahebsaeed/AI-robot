---
Page: Mobile Android App And WebSocket Protocol
Database: PFE Robot AI Documentation
Area: Android
Status: Ready
Tags: android, websocket, mobile, protocol
Related Files: android-app/, docs/MOBILE_PROTOCOL.md, core/mobile_gateway.py
---

# Mobile Android App And WebSocket Protocol

This page documents the custom Android app built in this project, not the default Yahboom MakerControl app.

## Android App Structure

| File | Role |
| --- | --- |
| `RobotApp.kt` | Compose UI with Map, Conversation, and Control tabs. |
| `RobotMap.kt` | Map rendering, zoom, pan, pose arrow. |
| `RobotViewModel.kt` | App state, commands, settings, speech control. |
| `RobotWebSocketClient.kt` | WebSocket connection and JSON messages. |
| `RobotModels.kt` | Data models. |
| `SpeechRecognizerManager.kt` | Android/Vosk speech recognition. |

## Connection

Default endpoint:

```text
ws://ROBOT_IP:8765
```

Robot side:

```bash
AI_MOBILE_ENABLED=1
AI_MOBILE_PORT=8765
AI_MOBILE_TOKEN='token'
./start_navigation.sh salle_robotique
```

## Tabs

| Tab | Purpose |
| --- | --- |
| Map | Shows map, robot pose, navigation goal, and status. |
| Conversation | Text and voice conversation with the robot. |
| Control | Speed slider, hold-to-drive buttons, parameters, robot mic switch. |

## Safety Controls

- Red STOP button in the top bar.
- Dead-man teleop heartbeat every 200 ms while pressed.
- Robot stops when button is released.
- Robot watchdog stops movement if heartbeat expires.
- Robot stops on WebSocket disconnect.

## Protocol Version

Protocol:

```text
v = 1
transport = WebSocket JSON
max frame = 2 MiB
max command = 500 characters
coordinates = ROS map frame
```

## Main Client Messages

| Type | Purpose |
| --- | --- |
| `hello` | Authenticate and establish session. |
| `command` | Send natural language command. |
| `stop` | Emergency stop. |
| `search_cancel` | Cancel current search only. |
| `teleop` | Manual dead-man movement. |
| `map_request` | Request map snapshot. |
| `robot_mic` | Enable or mute robot microphone. |
| `ping` | Application heartbeat. |

## Main Server Messages

| Type | Purpose |
| --- | --- |
| `hello_ack` | Connection accepted. |
| `ack` | Command accepted. |
| `processing` | Command processing started. |
| `response` | Final robot answer. |
| `status` | Current mode/phase. |
| `map` | Map PNG and metadata. |
| `robot_pose` | AMCL pose and active goal. |
| `robot_mic` | Robot mic state. |
| `error` | Protocol or robot error. |

## Images To Add In Notion

- Android Map tab screenshot.
- Android Conversation tab screenshot.
- Android Control tab screenshot.

