#!/bin/bash
# ==========================================================
# Yahboom Rosmaster X3 PLUS Navigation + AI
# Single launcher only.
#
# Keeps original project principle:
# - Yahboom navigation
# - AMCL localization
# - Astra RGB camera through ROS topic /camera/rgb/image_raw
# - Lidar through /scan
# - AI companion from /home/jetson/AI/main.py
#
# No extra .sh launchers.
# ==========================================================

MAP_NAME=${1:-salle_robotique}
CONTAINER="yahboom_container"
ROBOT_IP=$(hostname -I | awk '{print $1}')
MAP_FOLDER="/root/yahboomcar_ws/src/yahboomcar_nav/maps"
AI_FOLDER="/home/jetson/AI"
AI_LOG="/tmp/ai_companion.log"
AMCL_TOOL="$AI_FOLDER/tools/amcl_initializer.py"
LAST_AMCL_POSE_FILE="$AI_FOLDER/config/last_amcl_pose.json"
CLEANED_UP=0

run_in_docker() {
    docker exec "$CONTAINER" /bin/bash -lc "
        export ROBOT_TYPE=X3plus
        export LASER_TYPE=4ROS
        export ROS_MASTER_URI=http://$ROBOT_IP:11311
        export ROS_IP=$ROBOT_IP
        export DISPLAY=${DISPLAY:-:0}
        export QT_X11_NO_MITSHM=1
        unset ROS_HOSTNAME
        source /root/yahboomcar_ws/devel/setup.bash
        $1
    "
}

stop_host_ai() {
    pkill -f "/home/jetson/AI/main.py" >/dev/null 2>&1 || true
    pkill -f "robot_face.py" >/dev/null 2>&1 || true
}

prepare_rviz_display() {
    export DISPLAY="${DISPLAY:-:0}"
    export XAUTHORITY="${XAUTHORITY:-$HOME/.Xauthority}"
    if command -v xhost >/dev/null 2>&1; then
        xhost +SI:localuser:root >/dev/null 2>&1 || true
        xhost +local:root >/dev/null 2>&1 || true
        xhost +local:docker >/dev/null 2>&1 || true
    fi
}

stop_ai_bridges() {
    run_in_docker "pkill -9 -f '[a]i_cmd_vel_bridge.py' || true"
    run_in_docker "pkill -9 -f '[a]i_lidar_udp_bridge.py' || true"
    run_in_docker "pkill -9 -f '[r]os_rgb_camera_ai_bridge.py' || true"
    run_in_docker "pkill -9 -f '[a]i_camera_udp_bridge.py' || true"
    run_in_docker "pkill -9 -f '[r]os_head_camera_publisher.py' || true"
    run_in_docker "pkill -9 -f '[a]i_pose_udp_bridge.py' || true"
}

stop_rviz() {
    run_in_docker "pkill -9 -f 'rviz.*ai_navigation' || true"
    docker rm -f ai_companion_rviz >/dev/null 2>&1 || true
    pkill -f "rviz -d /tmp/ai_navigation" >/dev/null 2>&1 || true
}

stop_ros() {
    run_in_docker "pkill -9 -f 'rostopic pub.*cmd_vel' || true"
    run_in_docker "pkill -9 -f voice_Ctrl_send_mark.py || true"
    run_in_docker "pkill -9 -f yahboomcar_navigation.launch || true"
    run_in_docker "pkill -9 -f laser_astrapro_bringup.launch || true"
    run_in_docker "pkill -9 -f ydlidar_node || true"
    run_in_docker "pkill -9 -f rplidarNode || true"
    run_in_docker "pkill -9 -f sllidar_node || true"
    run_in_docker "pkill -9 -f move_base || true"
    run_in_docker "pkill -9 -f amcl || true"
    run_in_docker "pkill -9 -f map_server || true"
    run_in_docker "pkill -9 -f roslaunch || true"
    run_in_docker "pkill -9 -f roscore || true"
    run_in_docker "pkill -9 -f rosmaster || true"
}

stop_robot() {
    echo "[STOP] Stopping robot motion and stale /cmd_vel publishers..."
    run_in_docker "pkill -9 -f 'rostopic pub.*cmd_vel' || true"
    run_in_docker "pkill -9 -f '[a]i_persistent_move_base_goal.py|[a]i_nav_goal.py' || true"
    run_in_docker "
python3 - << 'PY'
import rospy
from actionlib_msgs.msg import GoalID
from geometry_msgs.msg import Twist

rospy.init_node('ai_emergency_stop', anonymous=True, disable_signals=True)
cancel_pub = rospy.Publisher('/move_base/cancel', GoalID, queue_size=1)
cmd_pub = rospy.Publisher('/cmd_vel', Twist, queue_size=1)
rospy.sleep(0.25)
cancel_pub.publish(GoalID())
zero = Twist()
rate = rospy.Rate(20)
for _ in range(14):
    cmd_pub.publish(zero)
    rate.sleep()
print('STOP_SENT')
PY
    " >/dev/null 2>&1 || true
}

stop_lidar_stack() {
    echo "[STOP] Stopping lidar motor and lidar ROS drivers..."
    run_in_docker "
for service in \$(rosservice list 2>/dev/null | grep -E '(^|/)stop_motor$|(^|/)stop_lidar$|(^|/)stop_scan$' || true); do
    rosservice call \"\$service\" '{}' >/dev/null 2>&1 || true
done
for node in /ydlidar_node /rplidarNode /sllidar_node; do
    rosnode kill \"\$node\" >/dev/null 2>&1 || true
done
pkill -9 -f '[a]i_lidar_udp_bridge.py' || true
pkill -9 -f 'ydlidar_node|rplidarNode|sllidar_node|laser_astrapro_bringup.launch' || true
    " >/dev/null 2>&1 || true
}

save_map_on_exit() {
    if [ "${AI_SAVE_MAP_ON_EXIT:-1}" = "0" ]; then
        echo "[MAP] Save skipped because AI_SAVE_MAP_ON_EXIT=0"
        return
    fi

    if [ "${AI_SAVE_STATIC_MAP_ON_EXIT:-0}" != "1" ]; then
        if ! run_in_docker "rostopic info /map 2>/dev/null | grep -Eqi '/slam_gmapping|gmapping|cartographer|hector|rtabmap|slam_toolbox'"; then
            echo "[MAP] /map is static navigation data, not live SLAM. Nothing new can be saved from start_navigation.sh."
            echo "[MAP] Use ./auto_scan.sh $MAP_NAME to update the occupancy map. Set AI_SAVE_STATIC_MAP_ON_EXIT=1 only to force re-saving the static map."
            return
        fi
    fi

    echo "[MAP] Saving live map update: $MAP_NAME"
    run_in_docker "
mkdir -p $MAP_FOLDER
TARGET_BASE=$MAP_FOLDER/$MAP_NAME
TEMP_BASE=$MAP_FOLDER/.${MAP_NAME}_nav_new_\$\$
BACKUP_BASE=$MAP_FOLDER/.${MAP_NAME}_nav_backup_\$(date +%Y%m%d_%H%M%S)
rm -f \${TEMP_BASE}.yaml \${TEMP_BASE}.pgm
OLD_SUM=''
if [ -s \${TARGET_BASE}.pgm ]; then
    OLD_SUM=\$(md5sum \${TARGET_BASE}.pgm | awk '{print \$1}')
fi
for ATTEMPT in 1 2 3; do
    echo '[MAP] map_saver attempt' \$ATTEMPT '/3'
    timeout 35s rosrun map_server map_saver -f \$TEMP_BASE --occ 65 --free 25
    STATUS=\$?
    if [ \$STATUS -eq 0 ] && [ -s \${TEMP_BASE}.yaml ] && [ -s \${TEMP_BASE}.pgm ]; then
        sed -i 's|^image:.*|image: $MAP_NAME.pgm|' \${TEMP_BASE}.yaml
        NEW_SUM=\$(md5sum \${TEMP_BASE}.pgm | awk '{print \$1}')
        if [ -n "\$OLD_SUM" ] && [ "\$OLD_SUM" = "\$NEW_SUM" ]; then
            echo '[MAP WARNING] Saved map image is identical to the previous file.'
        fi
        if [ -s \${TARGET_BASE}.pgm ] && [ -s \${TARGET_BASE}.yaml ]; then
            cp -f \${TARGET_BASE}.pgm \${BACKUP_BASE}.pgm
            cp -f \${TARGET_BASE}.yaml \${BACKUP_BASE}.yaml
            echo '[MAP] Previous map backup:' \${BACKUP_BASE}.yaml \${BACKUP_BASE}.pgm
        fi
        mv -f \${TEMP_BASE}.pgm \${TARGET_BASE}.pgm
        mv -f \${TEMP_BASE}.yaml \${TARGET_BASE}.yaml
        sync \${TARGET_BASE}.pgm \${TARGET_BASE}.yaml 2>/dev/null || sync
        echo '[MAP] Saved files:'
        ls -lh \${TARGET_BASE}.yaml \${TARGET_BASE}.pgm
        echo '[MAP] Map checksum:' \$(md5sum \${TARGET_BASE}.pgm | awk '{print \$1}')
        exit 0
    fi
    rm -f \${TEMP_BASE}.yaml \${TEMP_BASE}.pgm
    sleep 2
done
echo '[MAP ERROR] map_saver failed after three attempts.'
exit 1
    " || echo "[MAP WARNING] Map save failed; previous map files were left untouched."
}

sync_device_links_inside_docker() {
    echo "[DEV] Syncing device links inside Docker..."

    docker exec "$CONTAINER" /bin/bash -lc "
        rm -f /dev/camera_head /dev/camera_depth /dev/camera_usb /dev/ydlidar /dev/myserial /dev/myspeech 2>/dev/null || true

        if [ -e /dev/video0 ]; then ln -sf /dev/video0 /dev/camera_head; fi
        if [ -e /dev/video1 ]; then ln -sf /dev/video1 /dev/camera_depth; fi
        if [ -e /dev/video2 ]; then ln -sf /dev/video2 /dev/camera_usb; fi

        if [ -e /dev/ttyUSB0 ]; then ln -sf /dev/ttyUSB0 /dev/ydlidar; fi
        if [ -e /dev/ttyUSB1 ]; then ln -sf /dev/ttyUSB1 /dev/myserial; fi
        if [ -e /dev/ttyUSB2 ]; then ln -sf /dev/ttyUSB2 /dev/myspeech; fi

        chmod 777 /dev/video* /dev/camera_* /dev/ttyUSB* /dev/ydlidar /dev/myserial /dev/myspeech 2>/dev/null || true

        echo 'Docker camera devices:'
        ls -l /dev/camera_head /dev/camera_depth /dev/camera_usb /dev/video* 2>/dev/null || true
        echo 'Docker serial devices:'
        ls -l /dev/ydlidar /dev/myserial /dev/myspeech /dev/ttyUSB* 2>/dev/null || true
    "
}

wait_for_topic() {
    TOPIC="$1"
    TIMEOUT="$2"

    echo "Waiting for $TOPIC ..."

    for i in $(seq 1 "$TIMEOUT"); do
        if run_in_docker "rostopic list | grep -q '^$TOPIC$'"; then
            echo "  OK: $TOPIC"
            return 0
        fi
        sleep 1
    done

    echo "  WARNING: $TOPIC not found"
    return 1
}

wait_for_service() {
    local service="$1"
    local timeout="$2"

    echo "Waiting for $service ..."
    for _ in $(seq 1 "$timeout"); do
        if run_in_docker "rosservice list 2>/dev/null | grep -q '^$service$'"; then
            echo "  OK: $service"
            return 0
        fi
        sleep 1
    done
    echo "  ERROR: $service not found"
    return 1
}

run_amcl_tool() {
    if [ ! -f "$AMCL_TOOL" ]; then
        echo "[AMCL ERROR] Missing helper: $AMCL_TOOL"
        return 1
    fi

    local quoted_args=""
    local arg escaped
    for arg in "$@"; do
        printf -v escaped ' %q' "$arg"
        quoted_args+="$escaped"
    done

    docker exec -i "$CONTAINER" /bin/bash -lc "
        export ROBOT_TYPE=X3plus
        export LASER_TYPE=4ROS
        export ROS_MASTER_URI=http://$ROBOT_IP:11311
        export ROS_IP=$ROBOT_IP
        unset ROS_HOSTNAME
        source /root/yahboomcar_ws/devel/setup.bash
        python3 -$quoted_args
    " < "$AMCL_TOOL"
}

configure_amcl_global_search() {
    local min_particles="${AI_AMCL_MIN_PARTICLES:-500}"
    local max_particles="${AI_AMCL_MAX_PARTICLES:-5000}"
    local laser_max_beams="${AI_AMCL_LASER_MAX_BEAMS:-60}"
    local update_min_a="${AI_AMCL_UPDATE_MIN_A:-0.20}"
    echo "[AMCL] Configuring particle range: $min_particles..$max_particles"
    run_in_docker "
if command -v rosrun >/dev/null 2>&1 && rosrun dynamic_reconfigure dynparam get /amcl >/dev/null 2>&1; then
    rosrun dynamic_reconfigure dynparam set /amcl max_particles $max_particles >/dev/null 2>&1 || true
    rosrun dynamic_reconfigure dynparam set /amcl min_particles $min_particles >/dev/null 2>&1 || true
    rosrun dynamic_reconfigure dynparam set /amcl laser_max_beams $laser_max_beams >/dev/null 2>&1 || true
    rosrun dynamic_reconfigure dynparam set /amcl update_min_a $update_min_a >/dev/null 2>&1 || true
    rosrun dynamic_reconfigure dynparam set /amcl recovery_alpha_slow 0.001 >/dev/null 2>&1 || true
    rosrun dynamic_reconfigure dynparam set /amcl recovery_alpha_fast 0.1 >/dev/null 2>&1 || true
fi
    " || true
}

select_amcl_initial_pose() {
    AMCL_INITIAL_SOURCE=""
    AMCL_INITIAL_X_VALUE=""
    AMCL_INITIAL_Y_VALUE=""
    AMCL_INITIAL_YAW_VALUE=""

    if [ -n "${AI_AMCL_INITIAL_X:-}" ] || [ -n "${AI_AMCL_INITIAL_Y:-}" ] || [ -n "${AI_AMCL_INITIAL_YAW:-}" ]; then
        if [ -z "${AI_AMCL_INITIAL_X:-}" ] || [ -z "${AI_AMCL_INITIAL_Y:-}" ] || [ -z "${AI_AMCL_INITIAL_YAW:-}" ]; then
            echo "[AMCL ERROR] Set AI_AMCL_INITIAL_X, AI_AMCL_INITIAL_Y, and AI_AMCL_INITIAL_YAW together."
            return 1
        fi
        AMCL_INITIAL_SOURCE="configured"
        AMCL_INITIAL_X_VALUE="$AI_AMCL_INITIAL_X"
        AMCL_INITIAL_Y_VALUE="$AI_AMCL_INITIAL_Y"
        AMCL_INITIAL_YAW_VALUE="$AI_AMCL_INITIAL_YAW"
        return 0
    fi

    if [ "${AI_AMCL_USE_SAVED_POSE:-1}" = "0" ] || [ ! -f "$LAST_AMCL_POSE_FILE" ]; then
        return 1
    fi

    local saved_values
    saved_values=$(python3 - "$LAST_AMCL_POSE_FILE" "$MAP_NAME" "${AI_AMCL_SAVED_POSE_MAX_AGE:-604800}" <<'PY'
import json
import math
import sys
import time

path, map_name, max_age = sys.argv[1], sys.argv[2], float(sys.argv[3])
try:
    with open(path, "r", encoding="utf-8") as handle:
        data = json.load(handle)
    values = [float(data[key]) for key in ("x", "y", "yaw")]
    if data.get("map") != map_name or not all(math.isfinite(value) for value in values):
        raise ValueError("saved pose belongs to another map or is invalid")
    if time.time() - float(data.get("saved_at", 0.0)) > max_age:
        raise ValueError("saved pose is too old")
    print("{:.9f} {:.9f} {:.9f}".format(*values))
except Exception:
    raise SystemExit(1)
PY
    ) || return 1

    read -r AMCL_INITIAL_X_VALUE AMCL_INITIAL_Y_VALUE AMCL_INITIAL_YAW_VALUE <<< "$saved_values"
    AMCL_INITIAL_SOURCE="saved"
    return 0
}

trigger_global_localization() {
    echo "[AMCL] Distributing particles over the full map..."
    run_in_docker "rosservice call /global_localization '{}' >/dev/null" || return 1
    run_in_docker "rosservice call /request_nomotion_update '{}' >/dev/null 2>&1 || true"
    sleep 2
}

initialize_amcl() {
    local attempts="${AI_AMCL_ATTEMPTS:-3}"
    local spin_seconds="${AI_AMCL_SPIN_SECONDS:-12}"
    local refinement_spin_seconds="${AI_AMCL_REFINEMENT_SPIN_SECONDS:-5}"
    local spin_angular="${AI_AMCL_SPIN_ANGULAR:-0.65}"
    local check_timeout="${AI_AMCL_CHECK_TIMEOUT:-4}"
    local max_position_std="${AI_AMCL_MAX_POSITION_STD:-0.70}"
    local max_yaw_std="${AI_AMCL_MAX_YAW_STD:-0.55}"

    echo "[AMCL] Validating map, lidar data, odometry, and laser transforms..."
    run_amcl_tool preflight --timeout 12 --min-ranges 20 || return 1
    configure_amcl_global_search

    local has_initial_pose=0
    if select_amcl_initial_pose; then
        has_initial_pose=1
        echo "[AMCL] Seeding from $AMCL_INITIAL_SOURCE pose: x=$AMCL_INITIAL_X_VALUE y=$AMCL_INITIAL_Y_VALUE yaw=$AMCL_INITIAL_YAW_VALUE"
        run_amcl_tool publish \
            "$AMCL_INITIAL_X_VALUE" "$AMCL_INITIAL_Y_VALUE" "$AMCL_INITIAL_YAW_VALUE" \
            --xy-std "${AI_AMCL_INITIAL_XY_STD:-0.35}" \
            --yaw-std "${AI_AMCL_INITIAL_YAW_STD:-0.35}" || return 1

        echo "[AMCL] Checking seeded pose before moving the robot..."
        if run_amcl_tool check \
            --timeout "$check_timeout" \
            --max-position-std "$max_position_std" \
            --max-yaw-std "$max_yaw_std" \
            --samples 3; then
            echo "[AMCL] Seeded pose converged without a calibration rotation."
            save_amcl_pose_on_exit
            return 0
        fi
        echo "[AMCL WARNING] Seeded pose needs scan refinement."
    else
        trigger_global_localization || return 1
    fi

    local attempt angular duration redistributed
    for attempt in $(seq 1 "$attempts"); do
        redistributed=0
        if [ "$attempt" -ge 3 ] && [ $((attempt % 2)) -eq 1 ]; then
            trigger_global_localization || return 1
            redistributed=1
        fi
        angular="$spin_angular"
        if [ $((attempt % 2)) -eq 0 ]; then
            angular="-$spin_angular"
        fi

        if [ "$attempt" -eq 1 ] && [ "$has_initial_pose" = "0" ]; then
            duration="$spin_seconds"
        elif [ "$redistributed" = "1" ]; then
            duration="$spin_seconds"
        else
            duration="$refinement_spin_seconds"
        fi

        echo "[AMCL] Localization attempt $attempt/$attempts: ${duration}s scan at angular.z=$angular"
        run_amcl_tool spin --duration "$duration" --angular "$angular" || return 1
        run_in_docker "rosservice call /request_nomotion_update '{}' >/dev/null 2>&1 || true"
        sleep 1

        if run_amcl_tool check \
            --timeout "$check_timeout" \
            --max-position-std "$max_position_std" \
            --max-yaw-std "$max_yaw_std" \
            --samples 3; then
            echo "[AMCL] Localization converged successfully."
            save_amcl_pose_on_exit
            return 0
        fi

        if [ "$has_initial_pose" = "1" ]; then
            echo "[AMCL WARNING] The seeded pose has not converged yet; retaining its particle cloud for refinement."
            has_initial_pose=0
        elif [ "$redistributed" = "1" ]; then
            echo "[AMCL WARNING] Fresh global localization attempt $attempt did not converge."
        else
            echo "[AMCL WARNING] Pose is not converged yet; refining without resetting particles."
        fi
    done

    echo "[AMCL ERROR] Unable to establish a reliable pose after $attempts attempts."
    echo "[AMCL ERROR] For a fixed starting location, set AI_AMCL_INITIAL_X, AI_AMCL_INITIAL_Y, and AI_AMCL_INITIAL_YAW (radians)."
    return 1
}

save_amcl_pose_on_exit() {
    if [ "${AI_AMCL_SAVE_POSE:-1}" = "0" ] || [ ! -f "$AMCL_TOOL" ]; then
        return
    fi

    local output snapshot
    output=$(run_amcl_tool snapshot \
        --timeout 3 \
        --max-position-std "${AI_AMCL_MAX_POSITION_STD:-0.70}" \
        --max-yaw-std "${AI_AMCL_MAX_YAW_STD:-0.55}" 2>/dev/null) || return
    snapshot=$(printf '%s\n' "$output" | sed -n 's/^AMCL_SNAPSHOT //p' | tail -n 1)
    [ -n "$snapshot" ] || return

    mkdir -p "$(dirname "$LAST_AMCL_POSE_FILE")"
    python3 - "$LAST_AMCL_POSE_FILE" "$MAP_NAME" "$snapshot" <<'PY'
import json
import os
import sys

path, map_name, payload = sys.argv[1], sys.argv[2], sys.argv[3]
data = json.loads(payload)
data["map"] = map_name
temporary = path + ".tmp"
with open(temporary, "w", encoding="utf-8") as handle:
    json.dump(data, handle, sort_keys=True)
    handle.write("\n")
os.replace(temporary, path)
PY
    echo "[AMCL] Saved converged pose for the next startup: $LAST_AMCL_POSE_FILE"
}

startup_fail() {
    echo "[STARTUP ERROR] $1"
    stop_host_ai
    stop_robot
    stop_ai_bridges
    stop_ros
    exit 1
}

start_lidar_bridge() {
    echo "[AI] Starting lidar bridge /scan -> AI UDP 5010"

    docker exec -i "$CONTAINER" /bin/bash -lc "cat > /tmp/ai_lidar_udp_bridge.py" <<PYEOF
#!/usr/bin/env python3
import rospy
import socket
import json
import math
from sensor_msgs.msg import LaserScan

HOST_IP = "$ROBOT_IP"
HOST_PORT = 5010
TOPIC = "/scan"

sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)

def valid(r, msg):
    if r is None:
        return False
    if math.isnan(r) or math.isinf(r):
        return False
    if r < msg.range_min or r > msg.range_max:
        return False
    return True

def callback(msg):
    front = []
    all_ranges = []
    angle = msg.angle_min

    for r in msg.ranges:
        if valid(r, msg):
            all_ranges.append(float(r))
            if -0.52 <= angle <= 0.52:
                front.append(float(r))
        angle += msg.angle_increment

    min_all = min(all_ranges) if all_ranges else None
    min_front = min(front) if front else min_all

    payload = {"front": min_front, "all": min_all}
    sock.sendto(json.dumps(payload).encode("utf-8"), (HOST_IP, HOST_PORT))

rospy.init_node("ai_lidar_udp_bridge", anonymous=False)
rospy.Subscriber(TOPIC, LaserScan, callback, queue_size=1)
print("[LIDAR BRIDGE] Started /scan ->", HOST_IP, HOST_PORT, flush=True)
rospy.spin()
PYEOF

    run_in_docker "pkill -9 -f '[a]i_lidar_udp_bridge.py' || true"
    run_in_docker "nohup python3 /tmp/ai_lidar_udp_bridge.py > /tmp/ai_lidar_udp_bridge.log 2>&1 &"
}

start_cmd_bridge() {
    echo "[AI] Starting command bridge AI UDP 5020 -> /cmd_vel"

    docker exec -i "$CONTAINER" /bin/bash -lc "cat > /tmp/ai_cmd_vel_bridge.py" <<PYEOF
#!/usr/bin/env python3
import rospy
import socket
import json
import time
from geometry_msgs.msg import Twist

UDP_HOST = "0.0.0.0"
UDP_PORT = 5020

MAX_LINEAR = 0.22
MAX_ANGULAR = 0.65
COMMAND_HOLD_TIME = 0.9

last_cmd_time = 0.0
last_twist = Twist()
published_stop = True

def make_twist(direction, speed):
    try:
        speed = float(speed)
    except Exception:
        speed = 0.0

    speed = max(0.0, min(1.0, speed))

    t = Twist()

    if direction == "forward":
        t.linear.x = MAX_LINEAR * speed
    elif direction == "backward":
        t.linear.x = -MAX_LINEAR * speed
    elif direction == "left":
        t.angular.z = MAX_ANGULAR * speed
    elif direction == "right":
        t.angular.z = -MAX_ANGULAR * speed

    return t

def main():
    global last_cmd_time, last_twist, published_stop

    rospy.init_node("ai_cmd_vel_bridge", anonymous=False)

    cmd_pub = rospy.Publisher("/cmd_vel", Twist, queue_size=1)
    sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    sock.bind((UDP_HOST, UDP_PORT))
    sock.setblocking(False)

    print("[CMD BRIDGE] Started UDP 5020 -> /cmd_vel", flush=True)

    rate = rospy.Rate(20)

    while not rospy.is_shutdown():
        try:
            data, addr = sock.recvfrom(4096)
            msg = json.loads(data.decode("utf-8"))

            if msg.get("type") == "move":
                direction = str(msg.get("direction", "stop")).lower()
                speed = float(msg.get("speed", 0.0))

                last_twist = make_twist(direction, speed)
                last_cmd_time = time.time()
                published_stop = False

                print(f"[CMD BRIDGE] move direction={direction} speed={speed}", flush=True)

            elif msg.get("type") == "arm":
                print(f"[CMD BRIDGE] arm command={msg.get('command')}", flush=True)

        except BlockingIOError:
            pass
        except Exception as e:
            print("[CMD BRIDGE ERROR]", e, flush=True)

        if time.time() - last_cmd_time <= COMMAND_HOLD_TIME:
            cmd_pub.publish(last_twist)
            published_stop = False
        elif not published_stop:
            cmd_pub.publish(Twist())
            published_stop = True

        rate.sleep()

if __name__ == "__main__":
    main()
PYEOF

    run_in_docker "pkill -9 -f '[a]i_cmd_vel_bridge.py' || true"
    run_in_docker "nohup python3 /tmp/ai_cmd_vel_bridge.py > /tmp/ai_cmd_vel_bridge.log 2>&1 &"
}

start_rgb_camera_bridge() {
    echo "[AI] Starting RGB bridge /camera/rgb/image_raw -> AI UDP 5030"

    docker exec -i "$CONTAINER" /bin/bash -lc "cat > /tmp/ros_rgb_camera_ai_bridge.py" <<PYEOF
#!/usr/bin/env python3
import rospy
import cv2
import socket
import struct
import time
from sensor_msgs.msg import Image
from cv_bridge import CvBridge

HOST_IP = "$ROBOT_IP"
HOST_PORT = 5030
IMAGE_TOPIC = "/camera/rgb/image_raw"

WIDTH = 320
HEIGHT = 240
JPEG_QUALITY = 35
SEND_EVERY_N_FRAMES = 3

sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
bridge = CvBridge()

frame_id = 0
received = 0
sent = 0
last_report = time.time()

def callback(msg):
    global frame_id, received, sent, last_report

    received += 1

    if received % SEND_EVERY_N_FRAMES != 0:
        return

    try:
        frame = bridge.imgmsg_to_cv2(msg, desired_encoding="bgr8")
        frame = cv2.resize(frame, (WIDTH, HEIGHT))

        ok, jpg = cv2.imencode(".jpg", frame, [int(cv2.IMWRITE_JPEG_QUALITY), JPEG_QUALITY])
        if not ok:
            return

        data = jpg.tobytes()

        if len(data) > 12000:
            return

        packet = struct.pack("!I", frame_id) + data
        sock.sendto(packet, (HOST_IP, HOST_PORT))

        frame_id += 1
        sent += 1

        now = time.time()
        if now - last_report > 5:
            print(f"[RGB BRIDGE] received={received}, sent={sent}, last_size={len(data)}, target={HOST_IP}:{HOST_PORT}", flush=True)
            last_report = now

    except Exception as e:
        print("[RGB BRIDGE ERROR]", e, flush=True)

def main():
    rospy.init_node("ros_rgb_camera_ai_bridge", anonymous=False)
    print("[RGB BRIDGE] Started", flush=True)
    print("[RGB BRIDGE] Subscribing:", IMAGE_TOPIC, flush=True)
    print("[RGB BRIDGE] Sending to:", HOST_IP, HOST_PORT, flush=True)
    rospy.Subscriber(IMAGE_TOPIC, Image, callback, queue_size=1, buff_size=2**24)
    rospy.spin()

if __name__ == "__main__":
    main()
PYEOF

    run_in_docker "pkill -9 -f '[r]os_rgb_camera_ai_bridge.py' || true"
    run_in_docker "nohup python3 /tmp/ros_rgb_camera_ai_bridge.py > /tmp/ros_rgb_camera_ai_bridge.log 2>&1 &"
}

start_pose_bridge() {
    echo "[AI] Starting pose bridge /amcl_pose -> AI UDP 5040"

    docker exec -i "$CONTAINER" /bin/bash -lc "cat > /tmp/ai_pose_udp_bridge.py" <<PYEOF
#!/usr/bin/env python3
import rospy
import socket
import json
import math
from geometry_msgs.msg import PoseWithCovarianceStamped

HOST_IP = "$ROBOT_IP"
HOST_PORT = 5040
TOPIC = "/amcl_pose"

sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)

def yaw_from_quat(q):
    return math.atan2(2*(q.w*q.z+q.x*q.y), 1-2*(q.y*q.y+q.z*q.z))

def callback(msg):
    p = msg.pose.pose.position
    q = msg.pose.pose.orientation
    payload = {"x": p.x, "y": p.y, "yaw": yaw_from_quat(q)}
    try:
        sock.sendto(json.dumps(payload).encode("utf-8"), (HOST_IP, HOST_PORT))
    except Exception:
        pass

rospy.init_node("ai_pose_udp_bridge", anonymous=False)
rospy.Subscriber(TOPIC, PoseWithCovarianceStamped, callback, queue_size=1)
print("[POSE BRIDGE] Started /amcl_pose ->", HOST_IP, HOST_PORT, flush=True)
rospy.spin()
PYEOF

    run_in_docker "pkill -9 -f '[a]i_pose_udp_bridge.py' || true"
    run_in_docker "nohup python3 /tmp/ai_pose_udp_bridge.py > /tmp/ai_pose_udp_bridge.log 2>&1 &"
}

print_amcl_pose() {
    run_in_docker "
python3 - << 'PY'
import json
import math
import rospy
from geometry_msgs.msg import PoseWithCovarianceStamped

def yaw_from_quat(q):
    return math.atan2(2*(q.w*q.z+q.x*q.y), 1-2*(q.y*q.y+q.z*q.z))

rospy.init_node('ai_print_amcl_pose', anonymous=True, disable_signals=True)
msg = rospy.wait_for_message('/amcl_pose', PoseWithCovarianceStamped, timeout=4.0)
p = msg.pose.pose.position
q = msg.pose.pose.orientation
print('[AMCL] Robot pose: x={:.2f} y={:.2f} yaw={:.2f}'.format(p.x, p.y, yaw_from_quat(q)))
PY
    " || true
}

start_rviz() {
    if [ "${AI_AUTO_RVIZ:-1}" = "0" ]; then
        echo "[RVIZ] Auto RViz disabled because AI_AUTO_RVIZ=0"
        return
    fi

    echo "[RVIZ] Starting navigation view with map, AMCL pose, TF, lidar, and global plan..."

    docker exec -i "$CONTAINER" /bin/bash -lc "cat > /tmp/ai_navigation_auto.rviz" <<'RVIZEOF'
Panels:
  - Class: rviz/Displays
    Name: Displays
Visualization Manager:
  Class: ""
  Displays:
    - Alpha: 0.45
      Cell Size: 1
      Class: rviz/Grid
      Enabled: true
      Name: Grid
      Plane: XY
      Reference Frame: map
    - Alpha: 1
      Class: rviz/Map
      Color Scheme: map
      Draw Behind: true
      Enabled: true
      Name: Map
      Topic: /map
    - Alpha: 0.65
      Class: rviz/Map
      Color Scheme: costmap
      Enabled: true
      Name: Global Costmap
      Topic: /move_base/global_costmap/costmap
    - Class: rviz/RobotModel
      Enabled: true
      Name: Robot Model
      Robot Description: robot_description
      TF Prefix: ""
    - Alpha: 1
      Class: rviz/PoseWithCovariance
      Color: 0; 255; 0
      Enabled: true
      Name: Robot Position AMCL
      Topic: /amcl_pose
    - Alpha: 1
      Class: rviz/LaserScan
      Color: 255; 70; 70
      Color Transformer: FlatColor
      Enabled: true
      Name: Lidar Scan
      Queue Size: 10
      Size (m): 0.04
      Style: Points
      Topic: /scan
    - Alpha: 1
      Buffer Length: 1
      Class: rviz/Path
      Color: 0; 180; 255
      Enabled: true
      Line Style: Lines
      Line Width: 0.05
      Name: Global Plan
      Topic: /move_base/NavfnROS/plan
    - Class: rviz/TF
      Enabled: true
      Frame Timeout: 15
      Name: TF
      Show Arrows: true
      Show Axes: true
      Show Names: false
  Enabled: true
  Global Options:
    Background Color: 20; 20; 20
    Fixed Frame: map
    Frame Rate: 20
  Name: root
  Tools:
    - Class: rviz/Interact
    - Class: rviz/MoveCamera
    - Class: rviz/Select
    - Class: rviz/SetInitialPose
      Topic: /initialpose
    - Class: rviz/SetGoal
      Topic: /move_base_simple/goal
  Views:
    Current:
      Class: rviz/TopDownOrtho
      Name: Top Down
      Scale: 45
      Target Frame: map
Window Geometry:
  Height: 760
  Width: 1120
  X: 40
  Y: 40
RVIZEOF

    stop_rviz
    docker exec -d \
        -e DISPLAY="${DISPLAY:-:0}" \
        -e QT_X11_NO_MITSHM=1 \
        "$CONTAINER" /bin/bash -lc "
            export ROBOT_TYPE=X3plus
            export LASER_TYPE=4ROS
            export ROS_MASTER_URI=http://$ROBOT_IP:11311
            export ROS_IP=$ROBOT_IP
            unset ROS_HOSTNAME
            source /root/yahboomcar_ws/devel/setup.bash
            RVIZ_BIN=\$(command -v rviz 2>/dev/null || true)
            if [ -z \"\$RVIZ_BIN\" ] && [ -x /opt/ros/noetic/bin/rviz ]; then RVIZ_BIN=/opt/ros/noetic/bin/rviz; fi
            if [ -z \"\$RVIZ_BIN\" ]; then echo RVIZ_NOT_FOUND > /tmp/ai_rviz.log; exit 0; fi
            exec \"\$RVIZ_BIN\" -d /tmp/ai_navigation_auto.rviz > /tmp/ai_rviz.log 2>&1
        " >/dev/null 2>&1 || echo "[RVIZ] Failed to start RViz. Check /tmp/ai_rviz.log inside Docker."
}

start_ai() {
    echo "[AI] Starting AI companion..."

    stop_host_ai

    cd "$AI_FOLDER" || exit 1

    export DISPLAY=:0
    export XAUTHORITY="${XAUTHORITY:-$HOME/.Xauthority}"
    export QT_QPA_PLATFORM=xcb
    export TRANSFORMERS_OFFLINE=1
    export HF_HUB_OFFLINE=1
    export PYTHONUNBUFFERED=1
    export AI_MAP_NAME="$MAP_NAME"
    # Whisper chooses the most accurate model that fits the CUDA memory left
    # after Qwen has loaded: turbo -> small.en -> base.en -> tiny.en.
    export AI_WHISPER_MODEL="${AI_WHISPER_MODEL:-auto}"
    export AI_WHISPER_AUTO_DOWNLOAD="${AI_WHISPER_AUTO_DOWNLOAD:-0}"
    export AI_WHISPER_LANGUAGE="${AI_WHISPER_LANGUAGE:-en}"
    export AI_WHISPER_BEAM_SIZE="${AI_WHISPER_BEAM_SIZE:-5}"
    export AI_MIC_CALIBRATION_SECONDS="${AI_MIC_CALIBRATION_SECONDS:-1.5}"
    export AI_MIC_RECALIBRATION_SECONDS="${AI_MIC_RECALIBRATION_SECONDS:-45}"
    export AI_MIC_DYNAMIC_RATIO="${AI_MIC_DYNAMIC_RATIO:-1.8}"
    export AI_STT_ECHO_COOLDOWN="${AI_STT_ECHO_COOLDOWN:-0.45}"
    export AI_YOLO_MODEL="${AI_YOLO_MODEL:-$AI_FOLDER/yolo11n.pt}"
    export AI_OPEN_VOCAB="${AI_OPEN_VOCAB:-0}"
    export AI_OPEN_VOCAB_BACKEND="${AI_OPEN_VOCAB_BACKEND:-disabled}"
    export AI_OPEN_VOCAB_LOCAL_ONLY="${AI_OPEN_VOCAB_LOCAL_ONLY:-1}"
    export AI_RVIZ_HOST_DOCKER="${AI_RVIZ_HOST_DOCKER:-0}"
    export AI_RVIZ_AUTO_INSTALL="${AI_RVIZ_AUTO_INSTALL:-1}"
    export AI_RVIZ_DOCKER_IMAGE="${AI_RVIZ_DOCKER_IMAGE:-osrf/ros:noetic-desktop-full}"
    export AI_RVIZ_ALLOW_PULL="${AI_RVIZ_ALLOW_PULL:-0}"
    export AI_MOBILE_ENABLED="${AI_MOBILE_ENABLED:-1}"
    export AI_MOBILE_HOST="${AI_MOBILE_HOST:-0.0.0.0}"
    export AI_MOBILE_PORT="${AI_MOBILE_PORT:-8765}"
    export AI_MOBILE_TOKEN="${AI_MOBILE_TOKEN:-}"
    if [ "$AI_MOBILE_ENABLED" != "0" ] && [ -z "$AI_MOBILE_TOKEN" ]; then
        echo "[MOBILE WARNING] AI_MOBILE_TOKEN is empty. The WebSocket is open on the local network."
    fi
    if [ "${AI_OPEN_VOCAB}" != "0" ] && [ -f "$AI_FOLDER/yolov8s-worldv2.pt" ]; then
        export AI_YOLO_WORLD_MODEL="$AI_FOLDER/yolov8s-worldv2.pt"
    fi

    PYTHONUNBUFFERED=1 python3 "$AI_FOLDER/main.py" > "$AI_LOG" 2>&1 &

    echo "[AI] Started. Log: $AI_LOG"
}

cleanup() {
    if [ "$CLEANED_UP" = "1" ]; then
        exit 0
    fi
    CLEANED_UP=1
    trap - INT TERM

    echo ""
    echo "========================================"
    echo "[STOP] CTRL+C received"
    echo "========================================"

    stop_host_ai
    save_amcl_pose_on_exit
    stop_robot
    save_map_on_exit
    stop_lidar_stack

    stop_ai_bridges
    stop_rviz
    stop_ros

    stty sane 2>/dev/null || true
    tput cnorm 2>/dev/null || true

    echo "[DONE] Navigation + AI stopped."
    exit 0
}

trap cleanup INT TERM

echo "========================================"
echo " Yahboom Navigation + AI System"
echo " Map name: $MAP_NAME"
echo " Robot IP: $ROBOT_IP"
echo " ROS master: http://$ROBOT_IP:11311"
echo " AI folder: $AI_FOLDER"
echo "========================================"

echo "[1/12] Stopping old host AI..."
stop_host_ai
prepare_rviz_display
echo "[2/12] Restarting robot hardware service BEFORE Docker..."
sudo systemctl restart robot-init.service
sleep 3

echo "[3/12] Restarting Docker container..."
docker restart "$CONTAINER" >/dev/null 2>&1
sleep 3

echo "[4/12] Syncing device links inside Docker..."
sync_device_links_inside_docker

echo "[5/12] Cleaning old ROS and AI bridges..."
stop_ai_bridges
stop_ros
sleep 2

echo "[6/12] Checking map files..."
if ! run_in_docker "test -f $MAP_FOLDER/$MAP_NAME.yaml"; then
    echo "[ERROR] Map YAML not found: $MAP_FOLDER/$MAP_NAME.yaml"
    run_in_docker "ls -lh $MAP_FOLDER/*.yaml 2>/dev/null || true"
    exit 1
fi

echo "[7/12] Starting roscore..."
run_in_docker "roscore > /tmp/navigation_roscore.log 2>&1 &"
sleep 5

echo "[8/12] Starting Yahboom body, lidar, and Astra camera..."
run_in_docker "roslaunch yahboomcar_nav laser_astrapro_bringup.launch > /tmp/navigation_bringup.log 2>&1 &"
sleep 15
wait_for_topic "/scan" 30 || startup_fail "Lidar topic /scan did not start. Check /tmp/navigation_bringup.log."

echo "[9/12] Starting navigation with map: $MAP_NAME"
run_in_docker "roslaunch yahboomcar_nav yahboomcar_navigation.launch map:=$MAP_NAME use_rviz:=false > /tmp/navigation_stack.log 2>&1 &"
sleep 10

echo "[10/12] AMCL initialization and costmap preparation..."
wait_for_topic "/map" 30 || startup_fail "Map topic /map did not start. Check the map name and /tmp/navigation_stack.log."
wait_for_topic "/tf" 20 || startup_fail "TF topic /tf did not start."
wait_for_service "/global_localization" 30 || startup_fail "AMCL global localization service did not start."
wait_for_service "/move_base/clear_costmaps" 30 || startup_fail "move_base clear_costmaps service did not start."

if ! initialize_amcl; then
    startup_fail "AMCL could not determine a trustworthy robot position."
fi

echo "-> Confirming final AMCL robot position..."
print_amcl_pose

echo "-> Clearing move_base costmaps..."
run_in_docker "rosservice call /move_base/clear_costmaps '{}' >/dev/null 2>&1 || true"
sleep 2

echo "[11/12] Waiting for required topics..."
wait_for_topic "/camera/rgb/image_raw" 20 || true

echo "[12/12] Starting AI bridges and AI companion..."
start_lidar_bridge
start_cmd_bridge
start_rgb_camera_bridge
start_pose_bridge
sleep 3
start_rviz
start_ai

echo ""
echo "=================================================="
echo " NAVIGATION + AI SYSTEM IS RUNNING"
echo " Map: $MAP_NAME"
echo ""
echo "Single launcher:"
echo "  ~/start_navigation.sh $MAP_NAME"
echo ""
echo "Press CTRL+C here to stop AI, save map, and stop ROS."
echo ""
echo "Useful logs:"
echo "  tail -f /tmp/ai_companion.log"
echo "  ~/run_docker.sh"
echo "  tail -f /tmp/navigation_stack.log"
echo "  tail -f /tmp/navigation_bringup.log"
echo "  tail -f /tmp/ai_cmd_vel_bridge.log"
echo "  tail -f /tmp/ai_lidar_udp_bridge.log"
echo "  tail -f /tmp/ros_rgb_camera_ai_bridge.log"
echo ""
echo "Android application:"
echo "  WebSocket: ws://$ROBOT_IP:${AI_MOBILE_PORT:-8765}"
echo "  Pairing token: configure AI_MOBILE_TOKEN before launch"
echo "=================================================="
echo ""

while true; do
    sleep 1
done
