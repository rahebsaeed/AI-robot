# Robot Companion Mobile Protocol

## 1. Scope

This document defines protocol version 1 between the Android application and the
Rosmaster X3 Plus AI companion. The transport is one persistent WebSocket over
the robot's local Wi-Fi network.

- Default endpoint: `ws://ROBOT_IP:8765`
- Encoding: UTF-8 JSON text frames
- Maximum inbound frame: 2 MiB
- Maximum command text: 500 characters
- Coordinate frame: ROS `map`
- Angles: radians
- Distances: metres
- Time: Unix seconds

The development endpoint uses cleartext WebSocket. Use it only on an isolated,
trusted LAN. A deployed system must use `wss://` and certificate verification.

## 2. Connection Lifecycle

The first client message must be `hello` and must arrive within six seconds.

```json
{
  "v": 1,
  "type": "hello",
  "client_id": "android-550e8400-e29b-41d4-a716-446655440000",
  "token": "pairing-token",
  "app": "RobotCompanion"
}
```

The server validates `AI_MOBILE_TOKEN` when it is configured and answers:

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

The latest status and map follow the acknowledgement. OkHttp WebSocket ping
frames maintain transport liveness. The application reconnects with bounded
exponential backoff.

## 3. Client Messages

### 3.1 Natural-language command

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

The server immediately sends `ack`, then `processing`, then one final
`response`. The same command processor handles physical microphone commands.

### 3.2 Emergency stop

```json
{"v":1,"type":"stop","scope":"all","request_id":"uuid"}
```

`stop` bypasses the normal command queue. It cancels object search, cancels the
`move_base` goal, stops manual control and sends zero velocity.

### 3.3 Cancel only the current search

```json
{"v":1,"type":"search_cancel","request_id":"uuid"}
```

### 3.4 Dead-man manual control

```json
{
  "v": 1,
  "type": "teleop",
  "request_id": "uuid",
  "direction": "forward",
  "speed": 0.55,
  "active": true
}
```

Valid directions are `forward`, `backward`, `left`, `right` and `stop`.
The application sends an active frame every 200 ms while a control is held.
Release sends `active:false`. The robot watchdog stops movement when no active
frame is received for 650 ms or when the WebSocket disconnects. Speed is
normalized from `0.0` to `1.0` and clamped by the robot. The ROS bridge maps
`1.0` to its configured maximum linear and angular velocities.

### 3.5 Reload map

```json
{"v":1,"type":"map_request","request_id":"uuid"}
```

### 3.6 Application heartbeat

```json
{"v":1,"type":"ping","request_id":"uuid","ts":1781787056.52}
```

## 4. Server Messages

### 4.1 Acknowledgement

```json
{"v":1,"type":"ack","request_id":"uuid","state":"queued","timestamp":1781787056.52}
```

States include `queued`, `executing`, `active` and `stopped`.

### 4.2 Processing and final response

```json
{"v":1,"type":"processing","request_id":"uuid","text":"search for the bottle","source":"android","timestamp":1781787056.52}
```

```json
{"v":1,"type":"response","request_id":"uuid","text":"I found the bottle.","source":"android","status":"completed","timestamp":1781787062.10}
```

Final statuses include `completed`, `failed`, `rejected`, `cancelled` and
`stopped`.

### 4.3 Robot status

```json
{
  "v": 1,
  "type": "status",
  "mode": "searching",
  "phase": "navigating",
  "message": "Moving to search point 4/18",
  "target": "bottle",
  "waypoint_index": 4,
  "waypoint_total": 18,
  "searched_count": 3,
  "found": false,
  "current_waypoint": {"x": -1.5, "y": 4.5, "yaw": 0.0}
}
```

### 4.4 Map snapshot

```json
{
  "v": 1,
  "type": "map",
  "map_id": "0e91ad56ce46a10e",
  "name": "salle_robotique",
  "encoding": "png_base64",
  "image_base64": "iVBORw0KGgo...",
  "width": 800,
  "height": 800,
  "resolution": 0.05,
  "origin_x": -20.0,
  "origin_y": -20.0,
  "origin_yaw": 0.0,
  "frame": "map"
}
```

The map is sent at connection time and on `map_request`. The PNG is cached by
`map_id`; it is not streamed with pose updates.

### 4.5 Robot pose

```json
{
  "v": 1,
  "type": "robot_pose",
  "timestamp": 1781787056.52,
  "localized": true,
  "frame": "map",
  "x": -1.42,
  "y": 5.36,
  "yaw": 1.57,
  "pose_age": 0.08,
  "navigation_goal": {"x": 1.5, "y": 5.5, "yaw": 0.0, "frame":"map"}
}
```

Pose is published at up to 5 Hz. Identical state is reduced to 1 Hz.
The latest valid AMCL pose remains available while the robot is stationary;
`pose_age` indicates how long ago AMCL last changed that estimate.

### 4.6 Error

```json
{"v":1,"type":"error","request_id":"uuid","code":"AUTH_FAILED","message":"Invalid pairing token.","timestamp":1781787056.52}
```

Defined codes include `HELLO_REQUIRED`, `AUTH_FAILED`, `INVALID_COMMAND`,
`QUEUE_FULL`, `INVALID_TELEOP`, `MAP_UNAVAILABLE`, `UNKNOWN_TYPE` and
`INVALID_SERVER_MESSAGE`.

## 5. Map Projection

For a map with zero origin rotation:

```text
pixelX = (worldX - originX) / resolution
pixelY = height - 1 - (worldY - originY) / resolution
```

The Android implementation first rotates the world offset by `-origin_yaw`.
Screen zoom and pan are applied only after world-to-image projection. Zoom is
limited to `1x` through `6x`, and pan is clamped to the scaled image bounds so
the map cannot be dragged completely outside the viewport.

## 6. Safety Invariants

1. `stop` never waits behind AI inference or map search.
2. Starting teleoperation requests search cancellation and `move_base`
   cancellation before velocity heartbeats are emitted.
3. Manual velocity expires on the robot independently of Android.
4. WebSocket disconnection releases manual control.
5. Robot-side speed validation remains authoritative.
6. Natural-language movement continues through the existing LiDAR guard.
