#!/bin/bash
# ==========================================================
# Yahboom X3 Plus Professional Automatic Scan
# Persistent frontier memory, planned exploration, automatic
# completion detection, verified map saving, and safe stop.
# ==========================================================

MAP_ARG=${1:-home1}
CONTAINER="yahboom_container"
MAP_FOLDER="/root/yahboomcar_ws/src/yahboomcar_nav/maps"
ROBOT_IP=$(hostname -I | awk '{print $1}')
SCRIPT_DIR=$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)
EXPLORER_TOOL="$SCRIPT_DIR/tools/professional_explorer.py"
if [ ! -f "$EXPLORER_TOOL" ] && [ -f "/home/jetson/AI/tools/professional_explorer.py" ]; then
    EXPLORER_TOOL="/home/jetson/AI/tools/professional_explorer.py"
fi
MEMORY_FILE="$MAP_FOLDER/.${MAP_ARG}_exploration.json"
FINISHING=0
SAVED=0
CAN_SAVE_MAP=0
PLAN_SERVICE=""

if [[ ! "$MAP_ARG" =~ ^[A-Za-z0-9_-]+$ ]]; then
    echo "[ERROR] Map name may contain only letters, numbers, underscore, and hyphen."
    exit 2
fi

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

wait_for_topic() {
    local topic="$1"
    local timeout="$2"
    echo "Waiting for $topic ..."
    for _ in $(seq 1 "$timeout"); do
        if run_in_docker "rostopic list 2>/dev/null | grep -q '^$topic$'"; then
            echo "  OK: $topic"
            return 0
        fi
        sleep 1
    done
    echo "  ERROR: $topic not found"
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

start_move_base() {
    run_in_docker "roslaunch /root/yahboomcar_ws/src/yahboomcar_nav/launch/library/move_base.launch > /tmp/move_base_auto_scan.log 2>&1 &"
}

move_base_action_available() {
    run_in_docker "rostopic list 2>/dev/null | grep -q '^/move_base/status$'"
}

wait_for_move_base_action() {
    local timeout="$1"
    echo "Waiting for /move_base action server ..."
    for _ in $(seq 1 "$timeout"); do
        if move_base_action_available; then
            echo "  OK: move_base action navigation enabled"
            return 0
        fi
        sleep 1
    done
    echo "  ERROR: move_base action server not ready after ${timeout}s"
    return 1
}

stop_robot() {
    run_in_docker "
timeout 2s rostopic pub /move_base/cancel actionlib_msgs/GoalID '{}' -1 >/dev/null 2>&1 || true
for i in 1 2 3; do
    timeout 2s rostopic pub /cmd_vel geometry_msgs/Twist '{}' -1 >/dev/null 2>&1 || true
done
    " || true
}

stop_ros_nodes() {
    run_in_docker "pkill -9 -f '[p]rofessional_frontier_explorer' || true"
    run_in_docker "pkill -9 -f '[e]xplore_lite|[e]xplore ' || true"
    run_in_docker "pkill -9 -f '[e]scape_watchdog.py' || true"
    run_in_docker "pkill -9 -f '[m]ove_base' || true"
    run_in_docker "pkill -9 -f '[m]ap_saver' || true"
    run_in_docker "pkill -9 -f '[g]mapping' || true"
    run_in_docker "pkill -9 -f '[l]aser_astrapro_bringup' || true"
    run_in_docker "pkill -9 -f '[r]oslaunch' || true"
    run_in_docker "pkill -9 -f '[r]oscore' || true"
    run_in_docker "pkill -9 -f '[r]osmaster' || true"
}

existing_scan_is_complete() {
    run_in_docker "
test -s $MAP_FOLDER/$MAP_ARG.yaml &&
test -s $MAP_FOLDER/$MAP_ARG.pgm &&
test -s $MEMORY_FILE &&
python -c \"import json,sys; data=json.load(open('$MEMORY_FILE')); valid=(data.get('completed') and int(data.get('completion_version',0)) >= 2 and float(data.get('coverage',0)) >= float('${AUTO_SCAN_MIN_COMPLETION_COVERAGE:-0.40}') and len(data.get('visited_goals',[])) >= int('${AUTO_SCAN_MIN_COMPLETION_GOALS:-1}')); sys.exit(0 if valid else 1)\"
    " >/dev/null 2>&1
}

reset_existing_map() {
    if [ "${AUTO_SCAN_KEEP_EXISTING:-0}" = "1" ]; then
        return 0
    fi
    run_in_docker "
mkdir -p $MAP_FOLDER
TARGET_BASE=$MAP_FOLDER/$MAP_ARG
BACKUP_BASE=$MAP_FOLDER/.${MAP_ARG}_old_\$(date +%Y%m%d_%H%M%S)
if [ -e \${TARGET_BASE}.yaml ] || [ -e \${TARGET_BASE}.pgm ] || [ -e $MEMORY_FILE ]; then
    echo '[RESET] Existing map name found. Backing up and removing old files before new scan.'
    if [ -s \${TARGET_BASE}.yaml ]; then
        cp -f \${TARGET_BASE}.yaml \${BACKUP_BASE}.yaml
        rm -f \${TARGET_BASE}.yaml
    fi
    if [ -s \${TARGET_BASE}.pgm ]; then
        cp -f \${TARGET_BASE}.pgm \${BACKUP_BASE}.pgm
        rm -f \${TARGET_BASE}.pgm
    fi
    if [ -s $MEMORY_FILE ]; then
        cp -f $MEMORY_FILE \${BACKUP_BASE}_exploration.json
        rm -f $MEMORY_FILE
    fi
    echo '[RESET] Backup prefix:' \${BACKUP_BASE}
fi
    "
}

save_map() {
    if [ "$SAVED" -eq 1 ]; then
        return 0
    fi
    echo "[SAVE] Capturing the latest gmapping map: $MAP_ARG"
    if run_in_docker "
mkdir -p $MAP_FOLDER
TARGET_BASE=$MAP_FOLDER/$MAP_ARG
TEMP_BASE=$MAP_FOLDER/.${MAP_ARG}_new_\$\$
BACKUP_BASE=$MAP_FOLDER/.${MAP_ARG}_backup_\$(date +%Y%m%d_%H%M%S)
rm -f \${TEMP_BASE}.yaml \${TEMP_BASE}.pgm
OLD_SUM=''
if [ -s \${TARGET_BASE}.pgm ]; then
    OLD_SUM=\$(md5sum \${TARGET_BASE}.pgm | awk '{print \$1}')
fi
for ATTEMPT in 1 2 3; do
    echo '[SAVE] map_saver attempt' \$ATTEMPT '/3'
    timeout 35s rosrun map_server map_saver -f \$TEMP_BASE --occ 65 --free 25
    STATUS=\$?
    if [ "\${STATUS:-1}" -eq 0 ] && [ -s \${TEMP_BASE}.yaml ] && [ -s \${TEMP_BASE}.pgm ]; then
        sed -i 's|^image:.*|image: $MAP_ARG.pgm|' \${TEMP_BASE}.yaml
        NEW_SUM=\$(md5sum \${TEMP_BASE}.pgm | awk '{print \$1}')
        if [ -n "\$OLD_SUM" ] && [ "\$OLD_SUM" = "\$NEW_SUM" ]; then
            echo '[SAVE WARNING] New map image is identical to the previous saved map.'
        fi
        if [ -s \${TARGET_BASE}.pgm ] && [ -s \${TARGET_BASE}.yaml ]; then
            cp -f \${TARGET_BASE}.pgm \${BACKUP_BASE}.pgm
            cp -f \${TARGET_BASE}.yaml \${BACKUP_BASE}.yaml
            echo '[SAVE] Previous map backup:' \${BACKUP_BASE}.yaml \${BACKUP_BASE}.pgm
        fi
        mv -f \${TEMP_BASE}.pgm \${TARGET_BASE}.pgm
        mv -f \${TEMP_BASE}.yaml \${TARGET_BASE}.yaml
        sync \${TARGET_BASE}.pgm \${TARGET_BASE}.yaml 2>/dev/null || sync
        echo '[SAVE] Map files:'
        ls -lh \${TARGET_BASE}.yaml \${TARGET_BASE}.pgm
        echo '[SAVE] Map checksum:' \$(md5sum \${TARGET_BASE}.pgm | awk '{print \$1}')
        exit 0
    fi
    rm -f \${TEMP_BASE}.yaml \${TEMP_BASE}.pgm
    sleep 2
done
echo '[ERROR] map_saver failed after three attempts.'
exit 1
    "; then
        SAVED=1
        return 0
    fi
    return 1
}

finish_scan() {
    local reason="$1"
    local exit_status="${2:-0}"
    local save_before_stop="${3:-1}"
    local save_attempted=0
    if [ "$FINISHING" -eq 1 ]; then
        exit "$exit_status"
    fi
    FINISHING=1

    echo ""
    echo "========================================"
    echo "[FINISH] $reason"
    echo "========================================"
    if [ "$save_before_stop" = "1" ]; then
        echo "[SAVE] Starting verified map replacement before cleanup."
        save_attempted=1
        if [ "$CAN_SAVE_MAP" -eq 1 ]; then
            save_map || exit_status=1
        else
            echo "[SAVE] Skipped because gmapping never produced a usable /map topic."
        fi
    fi
    stop_robot
    sleep 1
    if [ "$SAVED" -ne 1 ] && [ "$CAN_SAVE_MAP" -eq 1 ]; then
        echo "[SAVE] Starting verified map replacement after safety stop."
        save_attempted=1
        save_map || exit_status=1
    elif [ "$SAVED" -ne 1 ] && [ "$save_attempted" -ne 1 ]; then
        echo "[SAVE] Skipped because gmapping never produced a usable /map topic."
    fi
    echo "[CLEAN] Stopping mapping and navigation nodes..."
    stop_robot
    stop_ros_nodes
    stty sane 2>/dev/null || true
    tput cnorm 2>/dev/null || true

    echo ""
    echo "[DONE] Automatic scan stopped safely."
    echo "[MAP] $MAP_FOLDER/$MAP_ARG.yaml"
    echo "[MEMORY] $MEMORY_FILE"
    exit "$exit_status"
}

on_interrupt() {
    finish_scan "Interrupted by user; saving partial map and exploration memory." 0 0
}

trap on_interrupt INT TERM

run_explorer() {
    if [ ! -f "$EXPLORER_TOOL" ]; then
        echo "[ERROR] Missing explorer: $EXPLORER_TOOL"
        return 1
    fi

    local args=(
        --map-name "$MAP_ARG"
        --memory "$MEMORY_FILE"
        --make-plan-service "$PLAN_SERVICE"
        --minimum-frontier-length "${AUTO_SCAN_MIN_FRONTIER_LENGTH:-0.35}"
        --minimum-clearance "${AUTO_SCAN_MIN_CLEARANCE:-0.35}"
        --visited-radius "${AUTO_SCAN_VISITED_RADIUS:-0.85}"
        --maximum-failures "${AUTO_SCAN_MAX_FAILURES:-2}"
        --plan-candidates "${AUTO_SCAN_PLAN_CANDIDATES:-6}"
        --goal-timeout "${AUTO_SCAN_GOAL_TIMEOUT:-60}"
        --progress-timeout "${AUTO_SCAN_PROGRESS_TIMEOUT:-15}"
        --minimum-goal-distance "${AUTO_SCAN_MIN_GOAL_DISTANCE:-0.10}"
        --minimum-near-goal-distance "${AUTO_SCAN_MIN_NEAR_GOAL_DISTANCE:-0.05}"
        --direct-max-linear "${AUTO_SCAN_DIRECT_MAX_LINEAR_SPEED:-${AUTO_SCAN_MAX_LINEAR_SPEED:-0.35}}"
        --direct-max-angular "${AUTO_SCAN_DIRECT_MAX_ANGULAR_SPEED:-${AUTO_SCAN_MAX_ANGULAR_SPEED:-1.00}}"
        --obstacle-stop-distance "${AUTO_SCAN_OBSTACLE_STOP_DISTANCE:-0.55}"
        --obstacle-slow-distance "${AUTO_SCAN_OBSTACLE_SLOW_DISTANCE:-0.90}"
        --completion-confirmations "${AUTO_SCAN_COMPLETION_CONFIRMATIONS:-3}"
        --minimum-completion-coverage "${AUTO_SCAN_MIN_COMPLETION_COVERAGE:-0.40}"
        --minimum-completion-goals "${AUTO_SCAN_MIN_COMPLETION_GOALS:-1}"
        --post-goal-scan-seconds "${AUTO_SCAN_POST_GOAL_SCAN_SECONDS:-0.8}"
        --recovery-turn-seconds "${AUTO_SCAN_RECOVERY_TURN_SECONDS:-0.8}"
        --recovery-drive-seconds "${AUTO_SCAN_RECOVERY_DRIVE_SECONDS:-2.0}"
        --recovery-drive-speed "${AUTO_SCAN_RECOVERY_DRIVE_SPEED:-0.25}"
        --recovery-min-front-clearance "${AUTO_SCAN_RECOVERY_MIN_FRONT_CLEARANCE:-0.75}"
        --completion-check-delay "${AUTO_SCAN_COMPLETION_CHECK_DELAY:-1.0}"
        --map-settle-time "${AUTO_SCAN_MAP_SETTLE_TIME:-0.6}"
        --max-weak-expansion-cycles "${AUTO_SCAN_MAX_WEAK_EXPANSIONS:-2}"
        --maximum-runtime "${AUTO_SCAN_MAX_RUNTIME:-180}"
        --maximum-goals "${AUTO_SCAN_MAX_GOALS:-10}"
    )
    if [ "${AUTO_SCAN_ALLOW_NEAR_GOALS:-1}" != "0" ]; then
        args+=(--allow-near-goals)
    fi
    if [ "${AUTO_SCAN_RESET_MEMORY:-0}" = "1" ] || [ "${AUTO_SCAN_REUSE_MEMORY:-0}" != "1" ]; then
        args+=(--reset-memory)
    fi

    local quoted_args=""
    local arg escaped
    for arg in "${args[@]}"; do
        printf -v escaped ' %q' "$arg"
        quoted_args+="$escaped"
    done

    docker exec -i "$CONTAINER" /bin/bash -lc "
        export ROBOT_TYPE=X3plus
        export LASER_TYPE=4ROS
        export ROS_MASTER_URI=http://$ROBOT_IP:11311
        export ROS_IP=$ROBOT_IP
        unset ROS_HOSTNAME
        export AUTO_SCAN_USE_MOVE_BASE=${AUTO_SCAN_USE_MOVE_BASE:-1}
        source /root/yahboomcar_ws/devel/setup.bash
        python -$quoted_args
    " < "$EXPLORER_TOOL"
}

echo "========================================"
echo " Yahboom Professional Automatic Scan"
echo " Map name: $MAP_ARG"
echo " Robot IP: $ROBOT_IP"
echo " Memory: $MEMORY_FILE"
echo "========================================"

echo "[1/9] Starting Docker container..."
docker start "$CONTAINER" >/dev/null 2>&1 || {
    echo "[ERROR] Cannot start $CONTAINER"
    exit 1
}

if [ "${AUTO_SCAN_SKIP_COMPLETED:-0}" = "1" ] && [ "${AUTO_SCAN_FORCE_NEW:-0}" != "1" ] && [ "${AUTO_SCAN_RESET_MEMORY:-0}" != "1" ] && existing_scan_is_complete; then
    stop_ros_nodes
    echo "[COMPLETE] Map $MAP_ARG was already completed and saved."
    echo "[COMPLETE] Set AUTO_SCAN_FORCE_NEW=1 to scan it again from zero."
    exit 0
fi

reset_existing_map

echo "[2/9] Cleaning old mapping/navigation processes..."
stop_ros_nodes
sleep 2

echo "[3/9] Restarting robot hardware service..."
sudo systemctl restart robot-init.service
sleep 3

echo "[4/9] Starting roscore..."
run_in_docker "roscore > /tmp/roscore_auto_scan.log 2>&1 &"
sleep 5

echo "[5/9] Starting the proven Yahboom bringup..."
run_in_docker "roslaunch yahboomcar_nav laser_astrapro_bringup.launch > /tmp/bringup_auto_scan.log 2>&1 &"
sleep 12

echo "[6/9] Starting gmapping..."
run_in_docker "roslaunch /root/yahboomcar_ws/src/yahboomcar_nav/launch/library/gmapping.launch > /tmp/gmapping_auto_scan.log 2>&1 &"
sleep 7

echo "[7/9] Starting move_base..."
start_move_base
sleep 3

echo "[8/9] Validating mapping and navigation interfaces..."
wait_for_topic "/scan" 30 || finish_scan "Lidar topic /scan is unavailable." 1
wait_for_topic "/map" 30 || finish_scan "Gmapping topic /map is unavailable." 1
CAN_SAVE_MAP=1
wait_for_topic "/tf" 20 || finish_scan "TF topic /tf is unavailable." 1
if wait_for_move_base_action "${AUTO_SCAN_MOVE_BASE_WAIT:-45}"; then
    MOVE_BASE_AVAILABLE=1
else
    echo "  WARNING: move_base action server is unavailable. Restarting move_base once..."
    run_in_docker "pkill -9 -f '[m]ove_base' || true"
    sleep 2
    start_move_base
    if wait_for_move_base_action "${AUTO_SCAN_MOVE_BASE_RETRY_WAIT:-45}"; then
        MOVE_BASE_AVAILABLE=1
    else
        MOVE_BASE_AVAILABLE=0
        echo "  ERROR: move_base did not become available after restart."
        run_in_docker "tail -n 40 /tmp/move_base_auto_scan.log 2>/dev/null || true"
        if [ "${AUTO_SCAN_REQUIRE_MOVE_BASE:-1}" = "1" ]; then
            CAN_SAVE_MAP=0
            finish_scan "move_base action server is unavailable; stopping instead of running slow internal navigation. Set AUTO_SCAN_REQUIRE_MOVE_BASE=0 to allow fallback." 1
        fi
        echo "  INFO: enabling internal A* planning and LiDAR-guarded path following."
    fi
fi

if run_in_docker "rosservice list 2>/dev/null | grep -q '^/move_base/clear_costmaps$'"; then
    echo "  OK: /move_base/clear_costmaps"
else
    echo "  INFO: costmap clearing service is unavailable in standalone mode."
fi

run_in_docker "
for n in /yahboom_joy /send_mark; do
    if rosnode list 2>/dev/null | grep -qx \"\$n\"; then
        rosnode kill \"\$n\" >/dev/null 2>&1 || true
    fi
done
" >/dev/null 2>&1 || true

PLAN_SERVICE=$(run_in_docker "rosservice list 2>/dev/null | grep -E '/make_plan$' | head -n 1" | tail -n 1)
if [ -n "$PLAN_SERVICE" ]; then
    echo "  OK: route planning service $PLAN_SERVICE"
else
    echo "  INFO: no exported make_plan service; internal A* route planning is enabled."
fi

echo "[9/9] Applying navigation safety parameters..."
if [ "${MOVE_BASE_AVAILABLE:-0}" = "1" ]; then
    run_in_docker "
    rosparam set /move_base/DWAPlannerROS/xy_goal_tolerance 0.35
    rosparam set /move_base/DWAPlannerROS/yaw_goal_tolerance 1.20
    rosparam set /move_base/DWAPlannerROS/latch_xy_goal_tolerance true
    rosparam set /move_base/DWAPlannerROS/max_vel_x ${AUTO_SCAN_MAX_LINEAR_SPEED:-0.35}
    rosparam set /move_base/DWAPlannerROS/min_vel_x 0.03
    rosparam set /move_base/DWAPlannerROS/max_vel_y 0.0
    rosparam set /move_base/DWAPlannerROS/min_vel_y 0.0
    rosparam set /move_base/DWAPlannerROS/max_trans_vel ${AUTO_SCAN_MAX_LINEAR_SPEED:-0.35}
    rosparam set /move_base/DWAPlannerROS/min_trans_vel 0.08
    rosparam set /move_base/DWAPlannerROS/max_vel_theta ${AUTO_SCAN_MAX_ANGULAR_SPEED:-1.00}
    rosparam set /move_base/DWAPlannerROS/max_rot_vel ${AUTO_SCAN_MAX_ANGULAR_SPEED:-1.00}
    rosparam set /move_base/DWAPlannerROS/min_rot_vel 0.20
    rosparam set /move_base/DWAPlannerROS/acc_lim_x 0.65
    rosparam set /move_base/DWAPlannerROS/acc_lim_theta 1.40
    rosparam set /move_base/DWAPlannerROS/sim_time 1.5
    rosparam set /move_base/DWAPlannerROS/vx_samples 20
    rosparam set /move_base/DWAPlannerROS/vtheta_samples 55
    rosparam set /move_base/DWAPlannerROS/path_distance_bias 42.0
    rosparam set /move_base/DWAPlannerROS/goal_distance_bias 10.0
    rosparam set /move_base/DWAPlannerROS/occdist_scale 0.08
    rosparam set /move_base/local_costmap/inflation_radius ${AUTO_SCAN_COSTMAP_INFLATION_RADIUS:-0.50}
    rosparam set /move_base/global_costmap/inflation_radius ${AUTO_SCAN_COSTMAP_INFLATION_RADIUS:-0.50}
    rosparam set /move_base/local_costmap/cost_scaling_factor ${AUTO_SCAN_COST_SCALING_FACTOR:-2.5}
    rosparam set /move_base/global_costmap/cost_scaling_factor ${AUTO_SCAN_COST_SCALING_FACTOR:-2.5}
    rosparam set /move_base/controller_frequency 5.0
    rosparam set /move_base/planner_frequency 1.0
    rosparam set /move_base/controller_patience 8.0
    rosparam set /move_base/planner_patience 6.0
    rosparam set /move_base/recovery_behavior_enabled true
    rosparam set /move_base/clearing_rotation_allowed false

    if rosrun dynamic_reconfigure dynparam get /move_base/DWAPlannerROS >/dev/null 2>&1; then
        rosrun dynamic_reconfigure dynparam set /move_base/DWAPlannerROS max_vel_x ${AUTO_SCAN_MAX_LINEAR_SPEED:-0.35} >/dev/null 2>&1 || true
        rosrun dynamic_reconfigure dynparam set /move_base/DWAPlannerROS min_vel_x 0.03 >/dev/null 2>&1 || true
        rosrun dynamic_reconfigure dynparam set /move_base/DWAPlannerROS max_trans_vel ${AUTO_SCAN_MAX_LINEAR_SPEED:-0.35} >/dev/null 2>&1 || true
        rosrun dynamic_reconfigure dynparam set /move_base/DWAPlannerROS min_trans_vel 0.08 >/dev/null 2>&1 || true
        rosrun dynamic_reconfigure dynparam set /move_base/DWAPlannerROS max_vel_theta ${AUTO_SCAN_MAX_ANGULAR_SPEED:-1.00} >/dev/null 2>&1 || true
        rosrun dynamic_reconfigure dynparam set /move_base/DWAPlannerROS max_rot_vel ${AUTO_SCAN_MAX_ANGULAR_SPEED:-1.00} >/dev/null 2>&1 || true
        rosrun dynamic_reconfigure dynparam set /move_base/DWAPlannerROS min_rot_vel 0.20 >/dev/null 2>&1 || true
        rosrun dynamic_reconfigure dynparam set /move_base/DWAPlannerROS xy_goal_tolerance 0.35 >/dev/null 2>&1 || true
        rosrun dynamic_reconfigure dynparam set /move_base/DWAPlannerROS yaw_goal_tolerance 1.20 >/dev/null 2>&1 || true
        rosrun dynamic_reconfigure dynparam set /move_base/DWAPlannerROS occdist_scale 0.08 >/dev/null 2>&1 || true
    fi
    rosservice call /move_base/clear_costmaps '{}' >/dev/null 2>&1 || true
    "
else
    echo "  INFO: skipping move_base tuning because the action server is not running."
fi

echo ""
echo "========================================"
echo " PROFESSIONAL AUTO EXPLORATION STARTED"
echo " - Chooses reachable frontiers by information gain and path cost"
echo " - Remembers visited, failed, and traversed positions"
echo " - Uses move_base when available, with internal A* fallback"
echo " - Set AUTO_SCAN_USE_MOVE_BASE=0 to force internal A* navigation"
echo " - Uses safe forward recovery probes when frontiers are still too near"
echo " - Uses bounded recovery for small rooms instead of long loops"
echo " - Saves and exits when no reachable new frontier remains"
echo " - CTRL+C safely saves a partial map"
echo "========================================"

run_explorer
EXPLORER_STATUS=$?

case "$EXPLORER_STATUS" in
    0)
        finish_scan "Exploration completed: no reachable unvisited frontiers remain." 0
        ;;
    2)
        finish_scan "Exploration paused before verified completion; saving partial map." 0
        ;;
    *)
        finish_scan "Explorer failed with status $EXPLORER_STATUS; saving recoverable partial map." 1
        ;;
esac
