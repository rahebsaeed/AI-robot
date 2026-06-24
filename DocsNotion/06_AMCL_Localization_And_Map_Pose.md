---
Page: AMCL Localization And Map Pose
Database: PFE Robot AI Documentation
Area: Robot
Status: Ready
Tags: amcl, localization, pose, map
Related Files: start_navigation.sh, tools/amcl_initializer.py, core/robot.py
---

# AMCL Localization And Map Pose

AMCL gives the robot its pose on the saved map. The current design avoids reusing a stale old pose by default.

## Current Default Behavior

By default:

- `AI_AMCL_USE_SAVED_POSE=0`
- `AI_AMCL_SAVE_POSE=0`
- `start_navigation.sh` ignores `config/last_amcl_pose.json`
- AMCL starts with `/global_localization`
- the robot rotates to collect scan data
- convergence is checked before navigation starts

This prevents the robot from showing the old pose after being manually moved to charge or another location.

## AMCL Startup Flow

1. Validate map, lidar, odometry, and laser TF.
2. Configure particle range.
3. If explicit initial pose is configured, publish it.
4. Otherwise call `/global_localization`.
5. Spin the robot and request no-motion updates.
6. Check covariance and `map -> base` transform.
7. Retry if needed.
8. Stop startup if pose is not trustworthy.

## Explicit Dock Pose

Use only when the robot always starts from a known fixed dock:

```bash
AI_AMCL_INITIAL_X=1.20 \
AI_AMCL_INITIAL_Y=-0.45 \
AI_AMCL_INITIAL_YAW=1.57 \
./start_navigation.sh salle_robotique
```

## Saved Pose Opt-In

Use only for controlled tests:

```bash
AI_AMCL_USE_SAVED_POSE=1 AI_AMCL_SAVE_POSE=1 ./start_navigation.sh salle_robotique
```

Do not use this when the robot may be physically moved while powered off.

## Pose Consumers

| Consumer | Use |
| --- | --- |
| `core/robot.py` | Caches AMCL pose from UDP `5040`. |
| Android app | Displays robot pose and navigation goal. |
| Search task | Computes distance to waypoints. |
| Places memory | Saves current `x`, `y`, and `yaw`. |
| Face UI | Shows navigation/search status. |

## Troubleshooting

```bash
docker exec -it yahboom_container /bin/bash -lc "rostopic echo /amcl_pose -n 1"
docker exec -it yahboom_container /bin/bash -lc "rostopic echo /scan -n 1"
docker exec -it yahboom_container /bin/bash -lc "rosrun tf tf_echo odom base_footprint"
```

If AMCL does not converge, check:

- map quality
- lidar ranges
- laser frame transform
- wrong `base_frame`
- robot moved during startup
- too few particles
- symmetric room with weak scan features

## Images To Add In Notion

- RViz screenshot before AMCL convergence.
- RViz screenshot after AMCL convergence with covariance visible.

