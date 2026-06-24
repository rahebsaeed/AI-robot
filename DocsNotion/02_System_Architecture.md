---
Page: System Architecture
Database: PFE Robot AI Documentation
Area: Architecture
Status: Ready
Tags: architecture, ros, docker, data-flow
Related Files: docs/algorithm_schema.svg, start_navigation.sh, main.py, core/
---

# System Architecture

The system is split into two runtime zones: the host Jetson AI zone and the ROS Docker zone.

## Host Jetson AI Zone

The host runs:

- `main.py`
- `robot_face.py`
- Qwen brain
- Whisper speech recognition
- YOLO inference
- UDP listeners for lidar, camera, and AMCL pose
- Android WebSocket gateway
- UDP command sender to ROS

## ROS Docker Zone

The Docker container `yahboom_container` runs:

- `roscore`
- Yahboom hardware bringup
- lidar driver
- Astra camera publisher
- map server
- AMCL
- move_base
- generated bridge scripts from `start_navigation.sh`
- RViz when enabled

## Core Data Flow

| Flow | Producer | Consumer | Transport |
| --- | --- | --- | --- |
| Lidar summary | `/scan` bridge | `Perceptions` | UDP `5010` |
| Movement command | `Robot` | `/cmd_vel` bridge | UDP `5020` |
| Camera image | `/camera/rgb/image_raw` bridge | `Perceptions` | UDP `5030` |
| AMCL pose | `/amcl_pose` bridge | `Robot` | UDP `5040` |
| Face UI events | `robot_face.py` | `main.py` | UDP `5006` |
| Face status | `main.py` | `robot_face.py` | UDP `5005` |
| Mobile app | Android | `MobileGateway` | WebSocket `8765` |

## Runtime Sequence

1. Launcher restarts hardware service and Docker.
2. ROS core and Yahboom bringup start.
3. Navigation stack loads the selected map.
4. AMCL is initialized with fresh global localization unless explicit pose is configured.
5. Costmaps are cleared.
6. UDP bridges start.
7. AI and face UI start.
8. Android gateway opens.
9. User commands enter one shared command bus.
10. Main loop routes commands to conversation, movement, place memory, search, or stop.

## Key Design Decisions

- Use one single launcher for navigation and AI: `start_navigation.sh`.
- Keep ROS inside Docker to match Yahboom image layout.
- Use lightweight UDP bridges instead of moving the whole AI into ROS.
- Keep emergency stop outside the LLM path.
- Treat robot position, map name, saved places, and camera facts as deterministic facts before asking the LLM.

## Failure Boundaries

| Boundary | Common Failure | Recovery |
| --- | --- | --- |
| Docker ROS | Missing topic or service | Check `/tmp/navigation_stack.log` and `/tmp/navigation_bringup.log`. |
| UDP bridges | No lidar/camera/pose data | Restart launcher and check bridge logs. |
| AMCL | Wrong or missing pose | Fresh global localization and spin; avoid saved pose by default. |
| Android WebSocket | Cannot connect | Check same Wi-Fi, robot IP, `AI_MOBILE_PORT`, and token. |
| Default MakerControl | External Wi-Fi issue | Check `ROS_IP` in `~/.bashrc` and `start_rosmaster_app`. |

## Images To Add In Notion

- Add `docs/algorithm_schema.svg`.
- Add a hand diagram showing Host Jetson, Docker ROS, Android phone, and ports.

