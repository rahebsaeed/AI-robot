---
Page: Robot Face UI And Operator Controls
Database: PFE Robot AI Documentation
Area: Operator UI
Status: Ready
Tags: face-ui, qt, controls, status
Related Files: robot_face.py, core/face_bridge.py, main.py
---

# Robot Face UI And Operator Controls

`robot_face.py` is the local Qt interface shown on the robot screen.

## Purpose

The face UI provides:

- visual robot personality
- status display
- object detection summary
- search progress
- stop/cancel controls
- RViz button
- robot microphone mute/unmute controls

## Communication

| Direction | Port | Format |
| --- | --- | --- |
| `main.py` to face | UDP `5005` | emotions, `msg:`, `status:` |
| face to `main.py` | UDP `5006` | JSON command messages |

## FaceBridge Events

`core/face_bridge.py` sends:

- emotions: `happy`, `sad`, `thinking`, `fear`, `neutral`
- messages: `msg:<text>`
- status payloads: `status:<json>`

It also broadcasts status events to Android clients through `MobileGateway`.

## Operator Commands

| UI Command | Effect |
| --- | --- |
| Stop search | Cancels current search and stops navigation. |
| RViz | Opens or raises RViz. |
| Mic off | Mutes physical robot microphone. |
| Mic on | Enables physical robot microphone. |

## Status Fields

Common status JSON fields:

| Field | Meaning |
| --- | --- |
| `mode` | ready, searching, found, stopped, manual, not_found. |
| `phase` | listening, processing, navigating, cancelled, rviz, etc. |
| `target` | Search target. |
| `waypoint_index` | Current waypoint number. |
| `waypoint_total` | Total waypoints. |
| `objects` | Latest camera objects. |
| `found` | Whether target has been found. |
| `message` | Human readable current status. |
| `can_talk` | Whether user can speak now. |

## Images To Add In Notion

- Photo of robot face during ready state.
- Photo during search state.
- Photo showing stop/RViz/mic buttons.

