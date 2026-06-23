#!/bin/bash
# ==========================================================
# Yahboom X3 Plus Manual Keyboard Scan
# Version: v3 Clean Save + Replace Existing Map
#
# Usage:
#   /home/jetson/keyboard_scan.sh map_name
#
# Example:
#   /home/jetson/keyboard_scan.sh home2
#
# Controls:
#   i     forward
#   ,     backward
#   j     turn left
#   l     turn right
#   u     forward + left
#   o     forward + right
#   m     backward + left
#   .     backward + right
#   k or SPACE  stop
#   q     quit and save
#   CTRL+C quit and save
#
# Speed:
#   w     increase linear speed
#   x     decrease linear speed
#   e     increase angular speed
#   c     decrease angular speed
# ==========================================================

MAP_ARG=${1:-home1}
CONTAINER="yahboom_container"
MAP_FOLDER="/root/yahboomcar_ws/src/yahboomcar_nav/maps"
ROBOT_IP=$(hostname -I | awk '{print $1}')
SAVED=0

run_in_docker() {
    docker exec "$CONTAINER" /bin/bash -lc "
        export ROBOT_TYPE=X3plus
        export LASER_TYPE=4ROS
        export ROS_MASTER_URI=http://$ROBOT_IP:11311
        export ROS_IP=$ROBOT_IP
        unset ROS_HOSTNAME
        source /root/yahboomcar_ws/devel/setup.bash
        $1
    "
}

stop_robot() {
    run_in_docker "rostopic pub /cmd_vel geometry_msgs/Twist '{}' -1 >/dev/null 2>&1 || true"
}

stop_ros_nodes() {
    run_in_docker "pkill -9 -f keyboard_teleop_scan.py || true"
    run_in_docker "pkill -9 -f map_saver || true"
    run_in_docker "pkill -9 -f gmapping || true"
    run_in_docker "pkill -9 -f laser_astrapro_bringup || true"
    run_in_docker "pkill -9 -f roslaunch || true"
    run_in_docker "pkill -9 -f roscore || true"
    run_in_docker "pkill -9 -f rosmaster || true"
}

save_map() {
    echo "[SAVE] Checking /map topic..."

    if ! run_in_docker "timeout 8s rostopic echo /map -n 1 >/tmp/last_map_check.txt 2>/tmp/map_check_error.txt"; then
        echo "[ERROR] No /map message received. Map cannot be saved."
        echo "[DEBUG] Check gmapping log:"
        echo "  ~/run_docker.sh"
        echo "  tail -f /tmp/gmapping_keyboard_scan.log"
        return 1
    fi

    echo "[SAVE] /map is active."
    echo "[SAVE] Removing old map files if they exist..."

    run_in_docker "
        mkdir -p $MAP_FOLDER
        rm -f $MAP_FOLDER/$MAP_ARG.yaml
        rm -f $MAP_FOLDER/$MAP_ARG.pgm
    "

    echo "[SAVE] Saving NEW map as: $MAP_ARG"

    run_in_docker "
        mkdir -p $MAP_FOLDER

        timeout 25s rosrun map_server map_saver -f $MAP_FOLDER/$MAP_ARG --occ 65 --free 25
        STATUS=\$?

        if [ \$STATUS -eq 124 ]; then
            echo '[ERROR] map_saver timed out after 25 seconds.'
            exit 124
        fi

        if [ \$STATUS -ne 0 ]; then
            echo '[ERROR] map_saver failed with status:' \$STATUS
            exit \$STATUS
        fi

        if [ ! -f $MAP_FOLDER/$MAP_ARG.yaml ]; then
            echo '[ERROR] YAML file was not created.'
            exit 1
        fi

        if [ ! -f $MAP_FOLDER/$MAP_ARG.pgm ]; then
            echo '[ERROR] PGM image file was not created.'
            exit 1
        fi

        sed -i 's|^image:.*|image: $MAP_ARG.pgm|' $MAP_FOLDER/$MAP_ARG.yaml

        echo '[SAVE] New map files:'
        ls -lh $MAP_FOLDER/$MAP_ARG.yaml $MAP_FOLDER/$MAP_ARG.pgm

        echo '[SAVE] YAML content:'
        cat $MAP_FOLDER/$MAP_ARG.yaml
    "
}

save_map_and_exit() {
    if [ "$SAVED" -eq 1 ]; then
        exit 0
    fi

    SAVED=1

    echo ""
    echo "========================================"
    echo "[STOP] Stopping robot..."
    echo "========================================"

    stop_robot
    sleep 1

    save_map

    echo "[CLEAN] Stopping ROS nodes..."
    stop_robot
    stop_ros_nodes

    stty sane 2>/dev/null || true
    tput cnorm 2>/dev/null || true

    echo ""
    echo "[DONE] Finished."
    echo "[MAP LOCATION]"
    echo "  $MAP_FOLDER/$MAP_ARG.yaml"
    echo "  $MAP_FOLDER/$MAP_ARG.pgm"
    echo ""
    exit 0
}

trap save_map_and_exit INT TERM

echo "========================================"
echo " Yahboom Manual Keyboard Scan v3"
echo " Map name: $MAP_ARG"
echo " Robot IP: $ROBOT_IP"
echo "========================================"

echo "[1/7] Starting Docker container..."
docker start "$CONTAINER" >/dev/null 2>&1

echo "[2/7] Cleaning old ROS nodes..."
stop_ros_nodes
sleep 2

echo "[3/7] Restarting robot service..."
sudo systemctl restart robot-init.service
sleep 3

echo "[4/7] Creating keyboard teleop script inside Docker..."

docker exec "$CONTAINER" /bin/bash -lc "cat > /tmp/keyboard_teleop_scan.py <<'PYEOF'
#!/usr/bin/env python
# -*- coding: utf-8 -*-

from __future__ import print_function

import rospy
import sys
import select
import termios
import tty

from geometry_msgs.msg import Twist

msg = '''
==================================================
 Yahboom Manual Keyboard Scan

 Controls:
   i     forward
   ,     backward
   j     turn left
   l     turn right
   u     forward + left
   o     forward + right
   m     backward + left
   .     backward + right
   k     stop
   SPACE stop
   q     quit and save
   CTRL+C quit and save

 Speed:
   w     increase linear speed
   x     decrease linear speed
   e     increase angular speed
   c     decrease angular speed
==================================================
'''

move_bindings = {
    'i': (1, 0),
    ',': (-1, 0),
    'j': (0, 1),
    'l': (0, -1),
    'u': (1, 1),
    'o': (1, -1),
    'm': (-1, -1),
    '.': (-1, 1),
}

speed_bindings = {
    'w': (1.1, 1.0),
    'x': (0.9, 1.0),
    'e': (1.0, 1.1),
    'c': (1.0, 0.9),
}

def get_key(timeout):
    tty.setraw(sys.stdin.fileno())
    rlist, _, _ = select.select([sys.stdin], [], [], timeout)

    if rlist:
        key = sys.stdin.read(1)
    else:
        key = ''

    termios.tcsetattr(sys.stdin, termios.TCSADRAIN, settings)
    return key

def publish_cmd(pub, linear, angular):
    twist = Twist()
    twist.linear.x = linear
    twist.angular.z = angular
    pub.publish(twist)

def main():
    global settings
    settings = termios.tcgetattr(sys.stdin)

    rospy.init_node('keyboard_teleop_scan')
    pub = rospy.Publisher('/cmd_vel', Twist, queue_size=1)

    linear_speed = 0.12
    angular_speed = 0.45

    target_linear = 0.0
    target_angular = 0.0

    print(msg)
    print('Current speed: linear %.2f m/s | angular %.2f rad/s' % (linear_speed, angular_speed))

    rate = rospy.Rate(10)

    try:
        while not rospy.is_shutdown():
            key = get_key(0.1)

            if key in move_bindings:
                x, th = move_bindings[key]
                target_linear = x * linear_speed
                target_angular = th * angular_speed

            elif key in speed_bindings:
                lin_mul, ang_mul = speed_bindings[key]
                linear_speed = linear_speed * lin_mul
                angular_speed = angular_speed * ang_mul

                if linear_speed < 0.04:
                    linear_speed = 0.04
                if linear_speed > 0.25:
                    linear_speed = 0.25

                if angular_speed < 0.15:
                    angular_speed = 0.15
                if angular_speed > 0.90:
                    angular_speed = 0.90

                print('Current speed: linear %.2f m/s | angular %.2f rad/s' % (linear_speed, angular_speed))

            elif key == ' ' or key == 'k':
                target_linear = 0.0
                target_angular = 0.0

            elif key == 'q':
                break

            elif key == '\x03':
                break

            publish_cmd(pub, target_linear, target_angular)
            rate.sleep()

    except Exception as e:
        print('Error:', e)

    finally:
        publish_cmd(pub, 0.0, 0.0)
        termios.tcsetattr(sys.stdin, termios.TCSADRAIN, settings)
        print('')
        print('Keyboard control stopped.')

if __name__ == '__main__':
    main()
PYEOF
chmod +x /tmp/keyboard_teleop_scan.py"

echo "[5/7] Starting roscore..."
run_in_docker "roscore > /tmp/roscore_keyboard_scan.log 2>&1 &"
sleep 5

echo "[6/7] Starting robot bringup..."
run_in_docker "roslaunch yahboomcar_nav laser_astrapro_bringup.launch > /tmp/bringup_keyboard_scan.log 2>&1 &"
sleep 12

echo "[6/7] Starting gmapping..."
run_in_docker "roslaunch /root/yahboomcar_ws/src/yahboomcar_nav/launch/library/gmapping.launch > /tmp/gmapping_keyboard_scan.log 2>&1 &"
sleep 7

echo "[7/7] Waiting for required ROS topics..."

for topic in /scan /map /cmd_vel; do
    echo "Waiting for $topic ..."
    ok=0

    for i in $(seq 1 25); do
        if run_in_docker "rostopic list | grep -q '^$topic$'"; then
            echo "  OK: $topic"
            ok=1
            break
        fi
        sleep 1
    done

    if [ "$ok" -eq 0 ]; then
        echo "  WARNING: $topic not found"
    fi
done

echo ""
echo "========================================"
echo " MANUAL SCAN STARTED"
echo " Drive the robot using the keyboard."
echo " Press CTRL+C or q to stop and save map."
echo "========================================"
echo ""

docker exec -it "$CONTAINER" /bin/bash -lc "
    export ROBOT_TYPE=X3plus
    export LASER_TYPE=4ROS
    export ROS_MASTER_URI=http://$ROBOT_IP:11311
    export ROS_IP=$ROBOT_IP
    unset ROS_HOSTNAME
    source /root/yahboomcar_ws/devel/setup.bash
    python /tmp/keyboard_teleop_scan.py
"

save_map_and_exit
