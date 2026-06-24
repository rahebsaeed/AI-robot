---
Page: Troubleshooting Known Problems
Database: PFE Robot AI Documentation
Area: Troubleshooting
Status: Ready
Tags: troubleshooting, known-issues, logs
Related Files: docs/README.md, start_navigation.sh, auto_scan.sh, core/
---

# Troubleshooting Known Problems

## AMCL Uses Old Pose

Cause:

- Saved pose was reused after robot was manually moved.

Current fix:

- Saved pose reuse is disabled by default.
- Fresh global localization and spin are used.

Check:

```bash
grep -n "AI_AMCL_USE_SAVED_POSE" start_navigation.sh
```

## AMCL Does Not Converge

Check:

```bash
docker exec -it yahboom_container /bin/bash -lc "rostopic echo /scan -n 1"
docker exec -it yahboom_container /bin/bash -lc "rostopic echo /amcl_pose -n 1"
docker exec -it yahboom_container /bin/bash -lc "rosrun tf tf_echo odom base_footprint"
```

Possible causes:

- bad map
- wrong starting area in symmetric room
- lidar data invalid
- TF problem
- robot rotates too fast or too little

## Auto Scan Does Not Move

Check:

```bash
tail -f /tmp/move_base_auto_scan.log
docker exec -it yahboom_container /bin/bash -lc "rostopic list | grep move_base"
docker exec -it yahboom_container /bin/bash -lc "rostopic echo /cmd_vel -n 5"
```

Common causes:

- move_base action server unavailable
- no reachable frontier
- robot too close to obstacle
- gmapping map too small
- costmap blocks all goals

## Auto Scan Too Slow

Prefer move_base:

```bash
AUTO_SCAN_REQUIRE_MOVE_BASE=1 AUTO_SCAN_USE_MOVE_BASE=1 ./auto_scan.sh testsalle
```

Increase speed only carefully:

```bash
AUTO_SCAN_MAX_LINEAR_SPEED=0.40 AUTO_SCAN_MAX_ANGULAR_SPEED=1.10 ./auto_scan.sh testsalle
```

## Map Not Saved

Check:

```bash
docker exec -it yahboom_container /bin/bash -lc "rostopic echo /map -n 1"
tail -f /tmp/gmapping_auto_scan.log
```

`start_navigation.sh` does not save static maps by default. Use `auto_scan.sh` to update a map.

## Search Sends Goals But Robot Does Not Move

Check:

```bash
tail -f /tmp/ai_companion.log
docker exec -it yahboom_container /bin/bash -lc "rostopic echo /move_base/status"
docker exec -it yahboom_container /bin/bash -lc "rostopic echo /cmd_vel"
```

Possible causes:

- AMCL pose not updating
- move_base rejected goals
- costmap blocked
- manual joystick node publishing competing `/cmd_vel`

## Android App Cannot Connect

For custom app:

- phone and robot same Wi-Fi
- correct robot IP
- port `8765` open
- token matches
- `AI_MOBILE_ENABLED=1`

For default MakerControl:

- check `ROS_IP` in `~/.bashrc`
- check `start_rosmaster_app`
- check active robot IP

## Head Camera Missing, Arm Camera Visible

Likely:

- camera index changed after reboot
- default app hardcoded wrong camera
- another process holds head camera

Check:

```bash
v4l2-ctl --list-devices
fuser -v /dev/video*
```

## Images To Add In Notion

- Screenshot for each major error message encountered during testing.

