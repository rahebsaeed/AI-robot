---
Page: Robot Control MoveBase CmdVel RViz
Database: PFE Robot AI Documentation
Area: Robot
Status: Ready
Tags: robot-control, move-base, cmd-vel, rviz
Related Files: core/robot.py, start_navigation.sh, core/mobile_control.py
---

# Robot Control MoveBase CmdVel RViz

`core/robot.py` is the main robot control API used by the AI.

## Movement Control

The AI sends UDP packets to port `5020`.

Example:

```json
{"type":"move","direction":"forward","speed":0.25}
```

The Docker command bridge converts this to ROS `/cmd_vel`.

## Valid Directions

```text
forward, backward, left, right, stop
```

## Arm Control

Arm commands are sent as UDP:

```json
{"type":"arm","command":"home"}
```

Valid active commands in the AI contract:

```text
home, pickup, drop, searching
```

## Navigation Goals

For map navigation, `Robot.goto_map(x, y, yaw)` sends a move_base goal in the `map` frame.

The system can navigate to:

- generated object-search waypoints
- saved named places
- Android selected navigation targets when implemented

## Stop Behavior

`Robot.stop()` sends repeated zero-speed commands. `cancel_navigation()` also cancels move_base and publishes zero velocity.

Global stop calls:

- cancel search
- stop mobile teleop
- send zero velocity
- cancel move_base goal
- update Android and face status

## RViz

RViz can be started by:

- launcher auto-start
- face UI button
- Android command support through mobile gateway

RViz shows:

- map
- global costmap
- robot model
- AMCL pose covariance
- lidar scan
- global plan
- TF

## RViz Fallbacks

`core/robot.py` supports:

- RViz inside `yahboom_container`
- custom command via `AI_RVIZ_COMMAND`
- host Docker fallback via `AI_RVIZ_HOST_DOCKER`
- SSH target via `AI_RVIZ_SSH_TARGET`

## Images To Add In Notion

- RViz screenshot with labeled displays.
- Screenshot of `/cmd_vel` echo while pressing Android control.

