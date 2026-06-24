---
Page: Operations Runbooks
Database: PFE Robot AI Documentation
Area: Operations
Status: Ready
Tags: runbook, commands, demo
Related Files: start_navigation.sh, auto_scan.sh, keyboard_scan.sh, tools/diagnose_rosmaster_default_app.sh
---

# Operations Runbooks

## Run AI Navigation

```bash
cd /home/jetson/AI
./start_navigation.sh salle_robotique
```

Expected:

- `/scan` OK
- `/map` OK
- `/tf` OK
- AMCL converged
- camera topic available
- AI companion started
- Android WebSocket printed

## Stop AI Navigation

Press:

```text
CTRL+C
```

Expected:

- AI stopped
- robot motion stopped
- costmaps cleared
- lidar driver stopped
- ROS nodes stopped

## Start Automatic Mapping

```bash
cd /home/jetson/AI
./auto_scan.sh testsalle
```

Expected:

- old map backed up if same name exists
- gmapping started
- move_base available
- explorer starts
- map saved at the end

## Start Manual Mapping

```bash
cd /home/jetson/AI
./keyboard_scan.sh testsalle
```

Use this if auto scan is blocked or room is too small.

## Watch Logs

```bash
tail -f /tmp/ai_companion.log
tail -f /tmp/navigation_stack.log
tail -f /tmp/navigation_bringup.log
tail -f /tmp/ros_rgb_camera_ai_bridge.log
tail -f /tmp/ai_cmd_vel_bridge.log
tail -f /tmp/ai_lidar_udp_bridge.log
```

## Check ROS Health

```bash
docker exec -it yahboom_container /bin/bash -lc "rostopic list"
docker exec -it yahboom_container /bin/bash -lc "rostopic echo /scan -n 1"
docker exec -it yahboom_container /bin/bash -lc "rostopic echo /amcl_pose -n 1"
docker exec -it yahboom_container /bin/bash -lc "rosservice list | grep move_base"
```

## Run Default MakerControl Diagnostics

```bash
bash ~/diagnose_rosmaster_default_app.sh | tee ~/rosmaster_default_app_diag.txt
```

## Demo Checklist

- Battery charged.
- Robot on floor, not touching obstacle.
- Correct map exists.
- Lidar spinning.
- Camera stream active.
- AMCL converges.
- Android phone and robot on same Wi-Fi.
- STOP button tested before demo movement.

## Images To Add In Notion

- Screenshot of every successful runbook command.

