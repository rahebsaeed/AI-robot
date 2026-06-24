#!/usr/bin/env bash
set -u

echo "========================================"
echo " Rosmaster Default App Diagnostics"
echo " Host: $(hostname)"
echo " User: $(whoami)"
echo " Date: $(date)"
echo "========================================"

section() {
    echo
    echo "---- $1 ----"
}

run() {
    echo "\$ $*"
    "$@" 2>&1 || true
}

section "Network"
run hostname -I
run ip -br addr
run ip route
run nmcli -t -f NAME,TYPE,DEVICE,STATE connection show --active

section "ROS Environment From Shell"
bash -lc 'grep -nE "ROS_MASTER_URI|ROS_IP|ROS_HOSTNAME|ROBOT_TYPE|LASER_TYPE" ~/.bashrc 2>/dev/null || true'
bash -lc 'source ~/.bashrc >/dev/null 2>&1 || true; env | grep -E "ROS_MASTER_URI|ROS_IP|ROS_HOSTNAME|ROBOT_TYPE|LASER_TYPE" | sort'

section "Startup Applications"
run find /home/jetson/.config/autostart /etc/xdg/autostart -maxdepth 1 -type f -print
run grep -RInE "start_rosmaster_app|rosmaster_main|MakerControl|laser_app|rosbridge|camera|video" \
    /home/jetson/.config/autostart /etc/xdg/autostart 2>/dev/null

section "Rosmaster App Files"
run ls -lah /home/jetson/Rosmaster/rosmaster/rosmaster_main.py
run grep -RInE "ROS_IP|ROS_MASTER_URI|0.0.0.0|127.0.0.1|192\\.|socket|bind|host|port|rosbridge|camera|video|VideoCapture" \
    /home/jetson/Rosmaster /home/jetson/software/laser_app 2>/dev/null

section "System Services"
run systemctl list-unit-files
run systemctl list-units --type=service --state=running
for service in robot-init.service start_rosmaster_app.service rosmaster.service yahboom.service; do
    if systemctl list-unit-files "$service" >/dev/null 2>&1; then
        echo
        echo "## $service"
        systemctl status "$service" --no-pager 2>&1 || true
        systemctl cat "$service" 2>&1 || true
    fi
done

section "Running App, ROS, Camera Processes"
run ps -ef
ps -ef | grep -Ei "rosmaster|MakerControl|rosbridge|laser_app|camera|video|mjpg|web_video|roslaunch|roscore|python" | grep -v grep || true

section "Listening Ports"
run ss -ltnup

section "Camera Devices"
run ls -l /dev/video* /dev/v4l/by-id/* /dev/v4l/by-path/* 2>/dev/null
if command -v v4l2-ctl >/dev/null 2>&1; then
    run v4l2-ctl --list-devices
    for dev in /dev/video*; do
        [ -e "$dev" ] || continue
        echo
        echo "## $dev"
        v4l2-ctl -d "$dev" --all 2>&1 | sed -n '1,80p' || true
    done
fi
run fuser -v /dev/video* 2>/dev/null

section "ROS Runtime"
if command -v rostopic >/dev/null 2>&1; then
    run rostopic list
    run rosnode list
fi

section "Docker Runtime"
if command -v docker >/dev/null 2>&1; then
    run docker ps --format 'table {{.Names}}\t{{.Status}}\t{{.Ports}}'
    if docker ps --format '{{.Names}}' | grep -qx yahboom_container; then
        docker exec yahboom_container /bin/bash -lc '
            echo "Docker hostname: $(hostname)"
            echo "Docker IPs: $(hostname -I)"
            source /root/yahboomcar_ws/devel/setup.bash >/dev/null 2>&1 || true
            env | grep -E "ROS_MASTER_URI|ROS_IP|ROS_HOSTNAME|ROBOT_TYPE|LASER_TYPE" | sort
            echo "-- docker /dev/video --"
            ls -l /dev/video* /dev/v4l/by-id/* /dev/v4l/by-path/* 2>/dev/null || true
            echo "-- docker ROS topics --"
            rostopic list 2>/dev/null || true
        ' 2>&1 || true
    fi
fi

echo
echo "========================================"
echo " Diagnostics complete"
echo " Copy this full output back to the PC."
echo "========================================"
