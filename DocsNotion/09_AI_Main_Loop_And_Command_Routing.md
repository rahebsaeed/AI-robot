---
Page: AI Main Loop And Command Routing
Database: PFE Robot AI Documentation
Area: AI
Status: Ready
Tags: main-loop, commands, routing
Related Files: main.py, core/command_bus.py, core/mobile_gateway.py
---

# AI Main Loop And Command Routing

`main.py` is the central coordinator. It receives commands, reads sensors, routes intents, updates the face UI, speaks responses, and sends robot actions.

## Created Components

| Component | File | Role |
| --- | --- | --- |
| `Brain` | `core/brain.py` | LLM and fast intent responses. |
| `Perceptions` | `core/perceptions.py` | Camera, lidar, microphone, STT, TTS, YOLO. |
| `Robot` | `core/robot.py` | Movement, navigation, map, AMCL pose, RViz. |
| `FaceBridge` | `core/face_bridge.py` | Face UI status and emotions. |
| `PlacesMemory` | `core/places_memory.py` | Named locations. |
| `MapSearchTask` | `core/search_tasks.py` | Object search workflow. |
| `CommandBus` | `core/command_bus.py` | Shared priority queue. |
| `MobileGateway` | `core/mobile_gateway.py` | Android WebSocket. |
| `MobileTeleopController` | `core/mobile_control.py` | Manual control watchdog. |
| `UiCommandListener` | `main.py` | Face UI button listener. |

## Command Sources

| Source | Path |
| --- | --- |
| Robot microphone | `MicrophoneCommandProducer -> CommandBus` |
| Android text or speech | `MobileGateway -> CommandBus` |
| Android emergency stop | `MobileGateway -> stop_all` |
| Android manual control | `MobileGateway -> MobileTeleopController` |
| Face UI stop button | `UiCommandListener -> cancel_search` |
| Face UI RViz button | `UiCommandListener -> Robot.launch_rviz` |
| Face UI mic buttons | `UiCommandListener -> Perceptions.set_mute` |

## Command Priority

`CommandBus` prioritizes:

1. Emergency stop.
2. Search cancel.
3. Movement.
4. Normal conversation and questions.

## Routing Order

For each command, `main.py` checks:

1. Save place.
2. Rename place.
3. Delete place.
4. List saved places.
5. Count saved places.
6. Map name questions.
7. Current saved-place questions.
8. Robot pose questions.
9. Current camera/vision questions.
10. Search request.
11. Conversation or movement through `Brain`.

This order prevents errors like treating "where are you" as a search for "you".

## Safety In The Loop

- Each operation has a generation number.
- Stop or teleop invalidates old operations.
- Movement is only executed if the command generation is still current.
- Lidar blocks unsafe forward movement.
- The robot microphone can be muted while moving.

## Images To Add In Notion

- Flowchart of command routing order.
- Screenshot of logs showing command source and response.

