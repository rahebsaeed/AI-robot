---
Page: Default Rosmaster MakerControl App
Database: PFE Robot AI Documentation
Area: Yahboom Default App
Status: Needs Robot Test
Tags: makercontrol, rosmaster, default-app, external-wifi
Related Files: tools/diagnose_rosmaster_default_app.sh
---

# Default Rosmaster MakerControl App

This page documents the default Yahboom/Rosmaster application, not the custom Android app in `android-app/`.

## What The Yahboom Docs Say

The default phone app is called:

```text
MakerControl
```

The robot starts a "large program" automatically after boot so the default app can control the robot.

Startup entry:

```text
start_rosmaster_app
```

Manual command:

```bash
python3 /home/jetson/Rosmaster/rosmaster/rosmaster_main.py
```

## Why External Wi-Fi Can Break It

When the robot changes from hotspot mode to an external Wi-Fi network:

- the robot IP changes
- `ROS_IP` may still point to the old IP
- `ROS_MASTER_URI` may use the wrong host
- the app may connect to the wrong address
- the server may bind or advertise the old address

The Yahboom docs say that when static IP/Wi-Fi changes, `~/.bashrc` must be updated so `ROS_IP` matches the active robot IP.

## Tomorrow Robot Checks

Run on the robot:

```bash
hostname -I
grep -nE "ROS_IP|ROS_MASTER_URI|ROS_HOSTNAME" ~/.bashrc
ps -ef | grep -Ei "rosmaster|MakerControl|rosbridge|laser_app|camera|video|roslaunch|python" | grep -v grep
ss -ltnup
```

## Full Diagnostic Script

Copy and run:

```bash
scp tools/diagnose_rosmaster_default_app.sh jetson@ROBOT_IP:/home/jetson/
ssh jetson@ROBOT_IP
bash ~/diagnose_rosmaster_default_app.sh | tee ~/rosmaster_default_app_diag.txt
```

Then copy the output back to the PC and analyze:

```bash
scp jetson@ROBOT_IP:/home/jetson/rosmaster_default_app_diag.txt .
```

## Camera Live Problem

Symptom:

- default app shows arm camera
- head camera live feed missing

Likely causes:

- `/dev/video*` order changed after reboot
- default app hardcodes a camera index
- head camera is occupied by another process
- Astra camera ROS node did not start
- app video topic or camera parameter points to wrong device

Check:

```bash
ls -l /dev/video* /dev/v4l/by-id/* /dev/v4l/by-path/*
v4l2-ctl --list-devices
fuser -v /dev/video*
```

## Images To Add In Notion

- Screenshot of MakerControl connection page.
- Screenshot of MakerControl camera page with wrong feed.
- Screenshot of `v4l2-ctl --list-devices` on the robot.

