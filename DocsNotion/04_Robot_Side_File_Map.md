---
Page: Robot Side File Map
Database: PFE Robot AI Documentation
Area: Architecture
Status: Ready
Tags: files, source-map, ownership
Related Files: main.py, core/, tools/, android-app/, docs/
---

# Robot Side File Map

This page explains where each important file belongs and what owns it.

## Project Root

| File or Folder | Purpose |
| --- | --- |
| `main.py` | Main AI command loop. |
| `start_navigation.sh` | Full launch and shutdown script for navigation plus AI. |
| `auto_scan.sh` | Professional automatic mapping launcher. |
| `keyboard_scan.sh` | Manual keyboard mapping fallback. |
| `robot_face.py` | Qt robot face interface. |
| `face_ui.launch` | Face UI launch helper. |
| `find_camera_index.py` | Local camera index probe. |
| `test_yolo_camera.py` | YOLO camera test script. |
| `requirements.txt` | Python dependencies. |
| `yolo11n.pt` | YOLO model file. |
| `config/places.json` | Saved named map places. |

## Core Python Modules

| File | Responsibility |
| --- | --- |
| `core/brain.py` | Qwen model loading, prompt, JSON response parsing, fast intent classification. |
| `core/perceptions.py` | Microphone, Whisper, YOLO, lidar UDP, camera UDP, speech output. |
| `core/robot.py` | UDP movement, arm commands, AMCL pose cache, map snapshots, RViz, move_base goals. |
| `core/search_tasks.py` | Object search intent parsing, waypoint selection, live camera search. |
| `core/mobile_gateway.py` | WebSocket server for Android. |
| `core/mobile_control.py` | Dead-man teleoperation watchdog. |
| `core/command_bus.py` | Shared priority queue for Android and robot microphone commands. |
| `core/places_memory.py` | Save, rename, delete, and list named map positions. |
| `core/face_bridge.py` | UDP bridge to robot face UI. |

## Tools

| File | Purpose |
| --- | --- |
| `tools/amcl_initializer.py` | AMCL preflight, initial pose publishing, spin, convergence checks, snapshots. |
| `tools/professional_explorer.py` | Frontier exploration, internal A*, memory, completion checks. |
| `tools/mock_robot_server.py` | PC simulator for Android app testing. |
| `tools/diagnose_rosmaster_default_app.sh` | Read-only robot diagnostic for Yahboom MakerControl/default app. |

## Android App

| File | Purpose |
| --- | --- |
| `android-app/app/src/main/java/com/pfe/robotcompanion/RobotViewModel.kt` | UI state and user actions. |
| `android-app/app/src/main/java/com/pfe/robotcompanion/data/RobotWebSocketClient.kt` | WebSocket client. |
| `android-app/app/src/main/java/com/pfe/robotcompanion/data/RobotModels.kt` | Data models. |
| `android-app/app/src/main/java/com/pfe/robotcompanion/ui/RobotApp.kt` | Main Compose UI. |
| `android-app/app/src/main/java/com/pfe/robotcompanion/ui/RobotMap.kt` | Map rendering, zoom, pan, pose display. |
| `android-app/app/src/main/java/com/pfe/robotcompanion/speech/` | Android speech recognition. |

## Backup Files

Files named `*_backup_*` are historical snapshots from earlier fixes. They are useful for comparison, but the active logic is in the non-backup files.

## Images To Add In Notion

- Screenshot of the repository tree.
- Screenshot of Jetson `/home/jetson/AI` after deployment.

