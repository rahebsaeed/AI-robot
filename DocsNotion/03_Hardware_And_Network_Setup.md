---
Page: Hardware And Network Setup
Database: PFE Robot AI Documentation
Area: Robot
Status: Ready
Tags: hardware, network, jetson, docker
Related Files: start_navigation.sh, auto_scan.sh, tools/diagnose_rosmaster_default_app.sh
---

# Hardware And Network Setup

## Hardware Components

| Component | Role |
| --- | --- |
| Yahboom Rosmaster X3 Plus chassis | Mobile base with mecanum drive. |
| Jetson Orin/NX class board | Runs Linux, Docker, AI, face UI, and control scripts. |
| Yahboom control board | Motor, arm, and hardware interface. |
| YDLidar or 4ROS lidar | `/scan` source for AMCL, move_base, and safety. |
| Astra/depth camera | ROS camera source for `/camera/rgb/image_raw`. |
| Robotic arm | Controlled through UDP arm commands. |
| USB microphone | Robot-side speech input. |
| Android phone | Mobile control and conversation interface. |

## Required Network Conditions

- Phone and robot must be on the same trusted LAN for both the custom app and MakerControl.
- For the custom Android app, the robot WebSocket is `ws://ROBOT_IP:8765`.
- For Yahboom MakerControl, the robot IP and `ROS_IP` must match the active network.
- Avoid exposing port `8765` or ROS ports to the public internet.

## Robot IP Discovery

On the robot:

```bash
hostname -I
ip -br addr
ip route
```

On the PC:

```bash
ping ROBOT_IP
ssh jetson@ROBOT_IP
```

## Docker Requirements

The project expects the Yahboom ROS Docker container:

```text
yahboom_container
```

Useful checks:

```bash
docker ps
docker start yahboom_container
docker exec -it yahboom_container /bin/bash
```

## Camera Device Risk

Linux may reorder `/dev/video0`, `/dev/video1`, and `/dev/video2` after reboot. This can make the default app or ROS camera node use the arm camera instead of the head camera.

Check tomorrow on the robot:

```bash
ls -l /dev/video* /dev/v4l/by-id/* /dev/v4l/by-path/*
v4l2-ctl --list-devices
fuser -v /dev/video*
```

## External Wi-Fi Risk

When connecting the robot to external Wi-Fi, update or verify:

```bash
grep -nE "ROS_IP|ROS_MASTER_URI|ROS_HOSTNAME" ~/.bashrc
hostname -I
```

If `ROS_IP` still points to an old hotspot IP, ROS clients and MakerControl may fail.

## Images To Add In Notion

- Photo of robot ports and connected USB devices.
- Screenshot of `v4l2-ctl --list-devices`.
- Screenshot of Wi-Fi settings and robot IP.

