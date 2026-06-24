---
Page: Project Executive Summary
Database: PFE Robot AI Documentation
Area: Overview
Status: Ready
Tags: summary, scope, stakeholders
Related Files: docs/README.md, main.py, start_navigation.sh, auto_scan.sh, android-app/
---

# Project Executive Summary

This project turns a Yahboom Rosmaster X3 Plus into a mobile AI companion with navigation, mapping, voice interaction, object search, Android control, and a robot face interface.

## Main Goal

The system lets a user start one launcher, localize the robot on a saved map, speak or type commands, control the robot safely, search for objects with the head camera, save named places, view the map on Android, and stop the robot at any time.

## Main Capabilities

| Capability | Description |
| --- | --- |
| Navigation | Uses ROS1, AMCL, map server, and move_base inside `yahboom_container`. |
| Mapping | Uses gmapping plus `auto_scan.sh` or `keyboard_scan.sh` to create `.yaml` and `.pgm` maps. |
| AI conversation | Uses Qwen2.5 1.5B on Jetson CUDA when available. |
| Voice input | Uses the robot USB microphone with Whisper. The mic starts muted by default. |
| Vision | Receives ROS camera frames, runs YOLO11, and describes visible objects. |
| Object search | Generates map waypoints and checks live camera while navigating. |
| Saved places | Stores named map locations in `config/places.json`. |
| Android app | Native Kotlin app with Map, Conversation, and Control tabs. |
| Face interface | Qt robot face shows emotions, status, objects, and operator buttons. |
| Emergency stop | Global stop from Android, robot face, or recognized voice command. |

## Current Project Roots

| Environment | Path |
| --- | --- |
| Development PC | `/home/raheb/Documents/PFE/AI` |
| Robot Jetson | `/home/jetson/AI` |
| ROS Docker workspace | `/root/yahboomcar_ws` inside `yahboom_container` |
| Map folder | `/root/yahboomcar_ws/src/yahboomcar_nav/maps` |

## Main User Workflows

1. Navigation and AI companion:

```bash
./start_navigation.sh salle_robotique
```

2. Automatic map creation:

```bash
./auto_scan.sh testsalle
```

3. Manual keyboard mapping:

```bash
./keyboard_scan.sh testsalle
```

4. Android app:

```text
Connect to ws://ROBOT_IP:8765
```

## Definition Of Done For The Project

- Robot localizes reliably after startup without reusing a wrong old pose.
- Robot can be stopped from every interface.
- Android map zoom and pan work while showing the last valid robot pose.
- Search commands move through the map and check camera frames while moving.
- Automatic scan creates a fresh map when the same map name is reused.
- The default Yahboom MakerControl app behavior is documented separately.

## Images To Add In Notion

- One photo of the complete robot.
- One screenshot of the Android app connected to the robot.
- One screenshot of RViz during navigation.
- One screenshot of the terminal after successful `start_navigation.sh`.

