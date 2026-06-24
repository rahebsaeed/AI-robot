---
Page: Launch Navigation Start Navigation
Database: PFE Robot AI Documentation
Area: Operations
Status: Ready
Tags: start-navigation, launcher, ros, startup
Related Files: start_navigation.sh, tools/amcl_initializer.py, docs/README.md
---

# Launch Navigation Start Navigation

`start_navigation.sh` is the main launcher for the AI companion and ROS navigation stack.

## Basic Command

```bash
./start_navigation.sh salle_robotique
```

If no map is provided, it defaults to:

```bash
salle_robotique
```

## Startup Steps

1. Stop old host AI and face processes.
2. Prepare X11 permissions for RViz and Qt face UI.
3. Restart `robot-init.service`.
4. Restart `yahboom_container`.
5. Sync device links inside Docker.
6. Stop old ROS and AI bridge processes.
7. Verify the map YAML exists.
8. Start `roscore`.
9. Start Yahboom body, lidar, and Astra camera bringup.
10. Wait for `/scan`.
11. Start navigation with selected map.
12. Wait for `/map`, `/tf`, `/global_localization`, and `/move_base/clear_costmaps`.
13. Run AMCL preflight and localization.
14. Clear move_base costmaps.
15. Wait for `/camera/rgb/image_raw`.
16. Start lidar, command, RGB camera, and pose UDP bridges.
17. Start RViz.
18. Start `main.py`.

## Generated Bridge Scripts

The launcher writes temporary scripts inside Docker:

| Script | Purpose |
| --- | --- |
| `/tmp/ai_lidar_udp_bridge.py` | `/scan` to UDP `5010`. |
| `/tmp/ai_cmd_vel_bridge.py` | UDP `5020` to `/cmd_vel`. |
| `/tmp/ros_rgb_camera_ai_bridge.py` | `/camera/rgb/image_raw` to UDP `5030`. |
| `/tmp/ai_pose_udp_bridge.py` | `/amcl_pose` to UDP `5040`. |

## Shutdown Behavior

CTRL+C runs cleanup:

1. Stop host AI and face UI.
2. Optionally save AMCL pose only if `AI_AMCL_SAVE_POSE=1`.
3. Stop robot motion and stale `/cmd_vel` publishers.
4. Clear costmaps by default.
5. Save map only if explicitly enabled with `AI_SAVE_MAP_ON_EXIT=1`.
6. Stop lidar motor and drivers.
7. Stop bridges, RViz, ROS nodes, and roscore.

## Important Logs

| Log | Meaning |
| --- | --- |
| `/tmp/ai_companion.log` | Main AI log. |
| `/tmp/navigation_bringup.log` | Yahboom body, lidar, and camera bringup. |
| `/tmp/navigation_stack.log` | map server, AMCL, move_base stack. |
| `/tmp/ai_cmd_vel_bridge.log` | UDP movement bridge. |
| `/tmp/ai_lidar_udp_bridge.log` | Lidar bridge. |
| `/tmp/ros_rgb_camera_ai_bridge.log` | RGB camera bridge. |
| `/tmp/ai_rviz.log` | RViz diagnostics. |

## Fast Health Check

```bash
tail -f /tmp/ai_companion.log
docker exec -it yahboom_container /bin/bash -lc "rostopic list"
docker exec -it yahboom_container /bin/bash -lc "rostopic echo /amcl_pose -n 1"
```

## Images To Add In Notion

- Screenshot of successful launcher output.
- Screenshot of bridge logs showing frames and messages.

