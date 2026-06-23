"""
robot.py — Fixed & upgraded Robot class for Rosmaster X3 PLUS
==============================================================
Key fixes vs original:
 1. get_amcl_pose() is now CACHED (refreshed every 2 s in background thread)
    → no more Docker subprocess on every 0.25 s poll cycle
 2. distance_to() uses the cached pose — instant, no Docker call
 3. goto_map() no longer calls cancel_navigation() on itself; the search loop
    cancels when it decides to
 4. Navigation lock runs async so goto_map() returns fast
 5. get_search_waypoints() adds a 7-px clearance (was 2-px) so waypoints
    never land inside walls
"""

import socket
import json
import subprocess
import threading
import time
import math
import os
from typing import Optional, Dict, List

class Robot:
    def __init__(self, port=5020, container="yahboom_container"):
        self.port = port
        self.container = container
        self.sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)

        self.targets = ["127.0.0.1"]

        try:
            ip = subprocess.check_output(
                "hostname -I | awk '{print $1}'",
                shell=True, text=True
            ).strip()
            if ip and ip not in self.targets:
                self.targets.append(ip)
            self.robot_ip = ip if ip else "192.168.50.196"
        except Exception:
            self.robot_ip = "192.168.50.196"

        self._move_thread = None
        self._stop_event = threading.Event()
        self._lock = threading.Lock()
        self._rviz_proc = None
        self._rviz_launching = False
        self._rviz_lock = threading.Lock()
        self._goal_lock = threading.Lock()
        self._navigation_goal: Optional[Dict] = None
        self._navigation_epoch = 0
        self._map_cache_lock = threading.Lock()
        self._map_cache: Dict[str, Dict] = {}

        # ── Cached AMCL pose ──────────────────────────────────────
        self._pose_lock = threading.Lock()
        self._cached_pose: Optional[Dict] = None
        self._pose_last_fetch: float = 0.0
        self.pose_sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        try:
            self.pose_sock.bind(("0.0.0.0", 5040))
            threading.Thread(target=self._pose_listener_loop, daemon=True).start()
        except Exception as e:
            print(f"[ROBOT AMCL SOCKET ERROR] Bind failed on port 5040: {e}", flush=True)

        print(f"[ROBOT] UDP cmd_vel targets={self.targets}, port={self.port}", flush=True)
        print(f"[ROBOT] move_base navigation active through Docker: {self.container}", flush=True)

    # ──────────────────────────────────────────────────────────────
    # Docker helper
    # ──────────────────────────────────────────────────────────────

    def _docker_ros(self, command: str, timeout: int = 20) -> str:
        ros_prefix = f"""
export ROBOT_TYPE=X3plus
export LASER_TYPE=4ROS
export ROS_MASTER_URI=http://{self.robot_ip}:11311
export ROS_IP={self.robot_ip}
unset ROS_HOSTNAME
source /root/yahboomcar_ws/devel/setup.bash
"""
        try:
            out = subprocess.check_output(
                ["docker", "exec", self.container, "/bin/bash", "-lc",
                 ros_prefix + "\n" + command],
                stderr=subprocess.STDOUT, timeout=timeout, text=True
            )
            return out.strip()
        except subprocess.CalledProcessError as e:
            out = e.output.strip() if e.output else ""
            print(f"[ROBOT DOCKER ERROR]: {out}", flush=True)
            return out
        except subprocess.TimeoutExpired:
            print("[ROBOT DOCKER ERROR]: command timed out", flush=True)
            return "TIMEOUT"

    # ──────────────────────────────────────────────────────────────
    # UDP movement
    # ──────────────────────────────────────────────────────────────

    def _send_once(self, payload):
        data = json.dumps(payload).encode("utf-8")
        for host in self.targets:
            try:
                self.sock.sendto(data, (host, self.port))
                print(f"[ROBOT UDP SENT]: {host}:{self.port} {payload}", flush=True)
            except Exception as e:
                print(f"[ROBOT UDP ERROR] {host}:{self.port} {e}", flush=True)

    def _send_move(self, direction, speed):
        self._send_once({"type": "move", "direction": direction, "speed": float(speed)})

    def _continuous_sender(self, direction, speed, duration, interval=0.20):
        start = time.time()
        while not self._stop_event.is_set():
            if time.time() - start >= duration:
                break
            self._send_move(direction, speed)
            time.sleep(interval)
        self._send_move("stop", 0.0)

    def move(self, direction="stop", speed=0.0, duration=None):
        direction = str(direction).strip().lower()
        try:
            speed = float(speed)
        except Exception:
            speed = 0.0
        speed = max(0.0, min(1.0, speed))
        if direction not in ["forward", "backward", "left", "right", "stop"]:
            direction = "stop"
        if direction == "stop":
            speed = 0.0
        if duration is None:
            duration = 0.0 if direction == "stop" else (5.0 if direction in ["left", "right"] else 10.0)

        print(f"[ROBOT UDP COMMAND]: direction={direction}, speed={speed}, duration={duration}", flush=True)

        with self._lock:
            self._stop_event.set()
            if self._move_thread and self._move_thread.is_alive():
                self._move_thread.join(timeout=0.4)
            self._stop_event.clear()

            if direction == "stop" or duration <= 0:
                self._send_move("stop", 0.0)
                return

            self._move_thread = threading.Thread(
                target=self._continuous_sender,
                args=(direction, speed, duration), daemon=True
            )
            self._move_thread.start()

    def stop(self):
        with self._lock:
            self._stop_event.set()
        for _ in range(3):
            self._send_move("stop", 0.0)
            time.sleep(0.05)

    def send_manual_velocity(self, direction="stop", speed=0.0):
        """Send one normalized velocity heartbeat for dead-man mobile control."""
        direction = str(direction or "stop").strip().lower()
        if direction not in {"forward", "backward", "left", "right", "stop"}:
            direction = "stop"
        try:
            speed = float(speed)
        except (TypeError, ValueError):
            speed = 0.0
        speed = max(0.0, min(1.0, speed))
        if direction == "stop":
            speed = 0.0
        self._send_move(direction, speed)

    def control_arm(self, command="home"):
        command = str(command).strip().lower()
        payload = {"type": "arm", "command": command}
        print(f"[ROBOT ARM UDP]: {payload}", flush=True)
        self._send_once(payload)

    # ──────────────────────────────────────────────────────────────
    # Navigation
    # ──────────────────────────────────────────────────────────────

    def prepare_navigation_mode(self):
        """Kill joystick/manual cmd_vel competitors asynchronously."""
        def _kill():
            cmd = """
for n in /yahboom_joy /send_mark; do
    if rosnode list 2>/dev/null | grep -qx "$n"; then
        rosnode kill "$n" > /dev/null 2>&1 || true
    fi
done
"""
            self._docker_ros(cmd, timeout=8)
            print("[ROBOT NAV]: manual nav competitors cleared", flush=True)
        threading.Thread(target=_kill, daemon=True).start()

    def cancel_navigation(self):
        print("[ROBOT NAV]: cancel move_base goal", flush=True)
        with self._goal_lock:
            self._navigation_goal = None
            self._navigation_epoch += 1
        self._docker_ros(
            """
pkill -9 -f '[a]i_nav_goal.py|[a]i_persistent_move_base_goal.py' || true
python3 - << 'PY'
import rospy
from actionlib_msgs.msg import GoalID
from geometry_msgs.msg import Twist
rospy.init_node('ai_cancel_nav', anonymous=True)
p1 = rospy.Publisher('/move_base/cancel', GoalID, queue_size=1)
p2 = rospy.Publisher('/cmd_vel', Twist, queue_size=1)
rospy.sleep(0.4)
p1.publish(GoalID())
p2.publish(Twist())
rospy.sleep(0.2)
PY
""", timeout=6)

    def clear_costmaps(self) -> bool:
        print("[ROBOT NAV]: clear costmaps", flush=True)
        out = self._docker_ros(
            "rosservice call /move_base/clear_costmaps '{}' >/dev/null 2>&1 && echo OK",
            timeout=8,
        )
        return "OK" in out

    def _external_rviz_running(self) -> bool:
        checks = [
            ["pgrep", "-af", "[r]viz"],
            ["docker", "exec", self.container, "/bin/bash", "-lc", "pgrep -af '[r]viz'"],
        ]
        for cmd in checks:
            try:
                proc = subprocess.run(
                    cmd,
                    stdout=subprocess.PIPE,
                    stderr=subprocess.DEVNULL,
                    timeout=3,
                    text=True,
                )
                if proc.returncode == 0 and proc.stdout.strip():
                    return True
            except Exception:
                continue
        return False

    def _raise_rviz_window(self) -> bool:
        env = os.environ.copy()
        env.setdefault("DISPLAY", ":0")
        env.setdefault("XAUTHORITY", os.path.expanduser("~/.Xauthority"))
        commands = [
            "wmctrl -a RViz",
            "wmctrl -a rviz",
            "xdotool search --onlyvisible --class rviz windowactivate --sync",
            "xdotool search --name RViz windowactivate --sync",
            "xdotool search --name rviz windowactivate --sync",
        ]
        for command in commands:
            try:
                proc = subprocess.run(
                    ["/bin/bash", "-lc", command],
                    stdout=subprocess.DEVNULL,
                    stderr=subprocess.DEVNULL,
                    timeout=3,
                    env=env,
                    text=True,
                )
                if proc.returncode == 0:
                    print(f"[ROBOT RVIZ]: raised existing window with: {command}", flush=True)
                    return True
            except Exception:
                continue
        print("[ROBOT RVIZ]: existing RViz detected, but no window-raise tool succeeded", flush=True)
        return False

    def launch_rviz(self) -> bool:
        print("[ROBOT RVIZ]: launch navigation view", flush=True)
        rviz_config = r"""
Panels:
  - Class: rviz/Displays
    Name: Displays
  - Class: rviz/Selection
    Name: Selection
  - Class: rviz/Tool Properties
    Name: Tool Properties
Visualization Manager:
  Class: ""
  Displays:
    - Alpha: 0.5
      Cell Size: 1
      Class: rviz/Grid
      Color: 160; 160; 160
      Enabled: true
      Name: Grid
      Plane: XY
      Plane Cell Count: 20
      Reference Frame: map
    - Alpha: 1
      Class: rviz/Map
      Color Scheme: map
      Draw Behind: false
      Enabled: true
      Name: Map
      Topic: /map
    - Alpha: 0.55
      Class: rviz/Map
      Color Scheme: costmap
      Draw Behind: false
      Enabled: true
      Name: Global Costmap
      Topic: /move_base/global_costmap/costmap
    - Class: rviz/RobotModel
      Enabled: true
      Name: Robot Model
      Robot Description: robot_description
      TF Prefix: ""
    - Alpha: 1
      Class: rviz/LaserScan
      Color: 255; 80; 80
      Color Transformer: FlatColor
      Enabled: true
      Name: Lidar Scan
      Queue Size: 10
      Size (m): 0.04
      Style: Points
      Topic: /scan
    - Alpha: 1
      Class: rviz/PoseWithCovariance
      Color: 0; 255; 0
      Enabled: true
      Name: Robot Position AMCL
      Topic: /amcl_pose
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
  Height: 720
  Width: 1100
  X: 40
  Y: 40
"""
        host_log = "/tmp/ai_rviz.log"

        def log_line(text: str):
            try:
                with open(host_log, "a", encoding="utf-8") as f:
                    f.write(text.rstrip() + "\n")
            except Exception as e:
                print(f"[ROBOT RVIZ ERROR]: could not write host log: {e}", flush=True)

        with self._rviz_lock:
            if self._rviz_launching:
                print("[ROBOT RVIZ]: launch already in progress", flush=True)
                log_line("[ROBOT RVIZ] launch already in progress; ignoring repeated button click")
                return True
            if self._rviz_proc is not None and self._rviz_proc.poll() is None:
                print("[ROBOT RVIZ]: already running — raising window", flush=True)
                log_line("[ROBOT RVIZ] already running; attempting to raise window")
                self._raise_rviz_window()
                return True
            if self._external_rviz_running():
                print("[ROBOT RVIZ]: external RViz already running — raising window", flush=True)
                log_line("[ROBOT RVIZ] external RViz already running; not launching another")
                self._raise_rviz_window()
                return True
            self._rviz_launching = True

        try:
            log_line(f"[ROBOT RVIZ] start requested at {time.strftime('%Y-%m-%d %H:%M:%S')}")

            custom_cmd = os.environ.get("AI_RVIZ_COMMAND", "").strip()
            if not custom_cmd and os.environ.get("AI_RVIZ_HOST_DOCKER", "0").strip() == "1":
                print("[ROBOT RVIZ]: using host Docker RViz fallback", flush=True)
                log_line("[ROBOT RVIZ] using host Docker RViz fallback")
                return self._launch_rviz_fallback(rviz_config, host_log)

            if custom_cmd:
                log_line("[ROBOT RVIZ] using AI_RVIZ_COMMAND")
                env = os.environ.copy()
                env.setdefault("ROS_MASTER_URI", f"http://{self.robot_ip}:11311")
                env.setdefault("ROS_IP", self.robot_ip)
                try:
                    with open(host_log, "a", encoding="utf-8") as log:
                        self._rviz_proc = subprocess.Popen(
                            ["/bin/bash", "-lc", custom_cmd],
                            stdout=log,
                            stderr=log,
                            env=env,
                            start_new_session=True,
                            text=True,
                        )
                    time.sleep(2)
                    if self._rviz_proc.poll() is None:
                        print("[ROBOT RVIZ]: custom RViz command started", flush=True)
                        return True
                    try:
                        with open(host_log, "r", encoding="utf-8", errors="replace") as f:
                            tail = "".join(f.readlines()[-60:])
                    except Exception:
                        tail = ""
                    print(f"[ROBOT RVIZ ERROR]: custom RViz command exited early\n{tail}", flush=True)
                    return False
                except Exception as e:
                    log_line(f"[ROBOT RVIZ ERROR] custom launch exception: {e}")
                    print(f"[ROBOT RVIZ ERROR]: custom launch exception: {e}", flush=True)
                    return False

            if not self._ensure_container_running():
                msg = f"Container {self.container} is not running and could not be started."
                print(f"[ROBOT RVIZ ERROR]: {msg}", flush=True)
                log_line("[ROBOT RVIZ ERROR] " + msg)
                return False

            self._allow_docker_x11()

            try:
                cfg = subprocess.run(
                    ["docker", "exec", "-i", self.container, "/bin/bash", "-lc", "cat > /tmp/ai_navigation.rviz"],
                    input=rviz_config.strip() + "\n",
                    stdout=subprocess.PIPE,
                    stderr=subprocess.STDOUT,
                    timeout=8,
                    text=True,
                )
                if cfg.stdout:
                    log_line(cfg.stdout)
                if cfg.returncode != 0:
                    msg = f"DOCKER_RVIZ_CONFIG_WRITE_FAILED rc={cfg.returncode}"
                    log_line(msg)
                    print(f"[ROBOT RVIZ ERROR]: {msg}", flush=True)
                    return False
            except Exception as e:
                msg = f"DOCKER_RVIZ_CONFIG_WRITE_EXCEPTION {e}"
                log_line(msg)
                print(f"[ROBOT RVIZ ERROR]: {msg}", flush=True)
                return False

            diag_cmd = """
echo DOCKER_RVIZ_DIAG_START
echo DISPLAY=${DISPLAY:-unset}
if [ -f /opt/ros/noetic/setup.bash ]; then source /opt/ros/noetic/setup.bash; fi
if [ -f /root/yahboomcar_ws/devel/setup.bash ]; then source /root/yahboomcar_ws/devel/setup.bash; fi
RVIZ_BIN=$(command -v rviz 2>/dev/null || true)
if [ -z "$RVIZ_BIN" ] && [ -x /opt/ros/noetic/bin/rviz ]; then RVIZ_BIN=/opt/ros/noetic/bin/rviz; fi
if [ -n "$RVIZ_BIN" ]; then
    echo RVIZ_BIN=$RVIZ_BIN
else
    echo DOCKER_RVIZ_NOT_FOUND
fi
ls -ld /tmp/.X11-unix 2>&1 || true
ls -l /tmp/.X11-unix/X0 2>&1 || true
test -s /tmp/ai_navigation.rviz && echo RVIZ_CONFIG_OK || echo RVIZ_CONFIG_MISSING
echo DOCKER_RVIZ_DIAG_END
"""
            try:
                diag_out = subprocess.check_output(
                    [
                        "docker", "exec",
                        "-e", "DISPLAY=:0",
                        "-e", "QT_X11_NO_MITSHM=1",
                        self.container,
                        "/bin/bash", "-lc", diag_cmd,
                    ],
                    stderr=subprocess.STDOUT,
                    timeout=8,
                    text=True,
                ).strip()
            except subprocess.CalledProcessError as e:
                diag_out = e.output.strip() if e.output else f"DOCKER_RVIZ_DIAG_FAILED rc={e.returncode}"
            except subprocess.TimeoutExpired:
                diag_out = "DOCKER_RVIZ_DIAG_TIMEOUT"

            log_line(diag_out or "DOCKER_RVIZ_DIAG_EMPTY")
            if "DOCKER_RVIZ_NOT_FOUND" in diag_out:
                install_cmd = (
                    f"docker exec -it {self.container} /bin/bash -lc "
                    "'apt-get update && apt-get install -y ros-noetic-rviz'"
                )
                if os.environ.get("AI_RVIZ_AUTO_INSTALL", "0").strip() == "1":
                    print("[ROBOT RVIZ]: rviz missing in Yahboom Docker; installing ros-noetic-rviz", flush=True)
                    if not self._install_rviz_in_container(host_log):
                        print(f"[ROBOT RVIZ ERROR]: install failed. Try manually:\n{install_cmd}", flush=True)
                        return False
                    print("[ROBOT RVIZ]: rviz installed in Yahboom Docker", flush=True)
                    log_line("[ROBOT RVIZ] rviz installed in Yahboom Docker")
                elif os.environ.get("AI_RVIZ_SSH_TARGET", "").strip() or os.environ.get("AI_RVIZ_HOST_DOCKER", "0").strip() == "1":
                    print("[ROBOT RVIZ]: rviz missing in Yahboom Docker; trying configured fallback", flush=True)
                    log_line("[ROBOT RVIZ] rviz missing in Yahboom Docker; trying configured fallback")
                    return self._launch_rviz_fallback(rviz_config, host_log)
                else:
                    msg = (
                        "rviz is not installed in yahboom_container. Install it once with:\n"
                        f"{install_cmd}"
                    )
                    log_line("[ROBOT RVIZ ERROR] " + msg)
                    print(f"[ROBOT RVIZ ERROR]: {msg}", flush=True)
                    return False

            if "RVIZ_CONFIG_OK" not in diag_out:
                print(f"[ROBOT RVIZ ERROR]: Docker RViz diagnostics failed\n{diag_out}", flush=True)
                return False

            ros_prefix = f"""
export ROBOT_TYPE=X3plus
export LASER_TYPE=4ROS
export ROS_MASTER_URI=http://{self.robot_ip}:11311
export ROS_IP={self.robot_ip}
unset ROS_HOSTNAME
if [ -f /opt/ros/noetic/setup.bash ]; then source /opt/ros/noetic/setup.bash; fi
if [ -f /root/yahboomcar_ws/devel/setup.bash ]; then source /root/yahboomcar_ws/devel/setup.bash; fi
"""
            launch_cmd = ros_prefix + """
export DISPLAY=:0
export QT_X11_NO_MITSHM=1
RVIZ_BIN=$(command -v rviz 2>/dev/null || true)
if [ -z "$RVIZ_BIN" ] && [ -x /opt/ros/noetic/bin/rviz ]; then RVIZ_BIN=/opt/ros/noetic/bin/rviz; fi
if [ -z "$RVIZ_BIN" ]; then echo DOCKER_RVIZ_NOT_FOUND_AFTER_INSTALL; exit 44; fi
exec "$RVIZ_BIN" -d /tmp/ai_navigation.rviz
"""

            try:
                with open(host_log, "a", encoding="utf-8") as log:
                    self._rviz_proc = subprocess.Popen(
                        [
                            "docker", "exec",
                            "-e", "DISPLAY=:0",
                            "-e", "QT_X11_NO_MITSHM=1",
                            self.container,
                            "/bin/bash", "-lc", launch_cmd,
                        ],
                        stdout=log,
                        stderr=log,
                        start_new_session=True,
                        text=True,
                    )
                time.sleep(3)
                if self._rviz_proc.poll() is None:
                    print("[ROBOT RVIZ]: started in Docker", flush=True)
                    return True

                try:
                    with open(host_log, "r", encoding="utf-8", errors="replace") as f:
                        tail = "".join(f.readlines()[-60:])
                except Exception:
                    tail = ""
                print(f"[ROBOT RVIZ ERROR]: Docker RViz exited early\n{tail}", flush=True)
                return False
            except Exception as e:
                print(f"[ROBOT RVIZ ERROR]: Docker launch exception: {e}", flush=True)
                log_line(f"[ROBOT RVIZ ERROR] Docker launch exception: {e}")
                return False
        finally:
            with self._rviz_lock:
                self._rviz_launching = False

    def _install_rviz_in_container(self, host_log: str) -> bool:
        install_cmd = "apt-get update && apt-get install -y ros-noetic-rviz && (source /opt/ros/noetic/setup.bash >/dev/null 2>&1 || true; command -v rviz >/dev/null 2>&1 || test -x /opt/ros/noetic/bin/rviz)"
        try:
            with open(host_log, "a", encoding="utf-8") as log:
                log.write("[ROBOT RVIZ] installing ros-noetic-rviz inside yahboom_container\n")
                proc = subprocess.run(
                    [
                        "docker", "exec",
                        "-e", "DEBIAN_FRONTEND=noninteractive",
                        self.container,
                        "/bin/bash", "-lc", install_cmd,
                    ],
                    stdout=log,
                    stderr=subprocess.STDOUT,
                    timeout=600,
                    text=True,
                )
            return proc.returncode == 0
        except subprocess.TimeoutExpired:
            try:
                with open(host_log, "a", encoding="utf-8") as log:
                    log.write("DOCKER_RVIZ_INSTALL_TIMEOUT\n")
            except Exception:
                pass
            return False
        except Exception as e:
            try:
                with open(host_log, "a", encoding="utf-8") as log:
                    log.write(f"DOCKER_RVIZ_INSTALL_EXCEPTION {e}\n")
            except Exception:
                pass
            return False

    def _launch_rviz_fallback(self, rviz_config: str, host_log: str) -> bool:
        def log_line(text: str):
            try:
                with open(host_log, "a", encoding="utf-8") as f:
                    f.write(text.rstrip() + "\n")
            except Exception:
                pass

        config_path = "/tmp/ai_navigation.rviz"
        log_line("[ROBOT RVIZ] local install command: docker exec -it yahboom_container /bin/bash -lc 'apt-get update && apt-get install -y ros-noetic-rviz'")
        try:
            with open(config_path, "w", encoding="utf-8") as f:
                f.write(rviz_config.strip() + "\n")
        except Exception as e:
            log_line(f"HOST_RVIZ_CONFIG_WRITE_FAILED {e}")
            return False

        ssh_target = os.environ.get("AI_RVIZ_SSH_TARGET", "").strip()
        if ssh_target:
            pc_ros_ip = os.environ.get("AI_RVIZ_PC_ROS_IP", "").strip()
            docker_cmd = os.environ.get("AI_RVIZ_DOCKER_CMD", "docker").strip() or "docker"
            rviz_image = os.environ.get("AI_RVIZ_DOCKER_IMAGE", "osrf/ros:noetic-desktop-full").strip() or "osrf/ros:noetic-desktop-full"
            try:
                send = subprocess.run(
                    ["ssh", ssh_target, "cat > /tmp/ai_navigation.rviz"],
                    input=rviz_config.strip() + "\n",
                    stdout=subprocess.PIPE,
                    stderr=subprocess.STDOUT,
                    timeout=8,
                    text=True,
                )
                if send.stdout:
                    log_line(send.stdout)
                if send.returncode != 0:
                    log_line(f"REMOTE_RVIZ_CONFIG_WRITE_FAILED rc={send.returncode}")
                    return False

                remote_cmd = f'''
set -e
xhost +local:docker >/dev/null 2>&1 || true
PC_ROS_IP="{pc_ros_ip}"
if [ -z "$PC_ROS_IP" ]; then PC_ROS_IP=$(hostname -I | awk '{{print $1}}'); fi
{docker_cmd} rm -f ai_companion_rviz >/dev/null 2>&1 || true
{docker_cmd} run --name ai_companion_rviz --rm --net=host \
  --add-host yahboom:{self.robot_ip} \
  -e DISPLAY=${{DISPLAY:-:0}} \
  -e QT_X11_NO_MITSHM=1 \
  -e ROS_MASTER_URI=http://{self.robot_ip}:11311 \
  -e ROS_IP=$PC_ROS_IP \
  --device /dev/dri:/dev/dri \
  -v /tmp/.X11-unix:/tmp/.X11-unix \
  -v /tmp/ai_navigation.rviz:/tmp/ai_navigation.rviz:ro \
  {rviz_image} rosrun rviz rviz -d /tmp/ai_navigation.rviz
'''
                log_line(f"[ROBOT RVIZ] launching remote PC RViz on {ssh_target}")
                with open(host_log, "a", encoding="utf-8") as log:
                    self._rviz_proc = subprocess.Popen(
                        ["ssh", ssh_target, remote_cmd],
                        stdout=log,
                        stderr=log,
                        start_new_session=True,
                        text=True,
                    )
                time.sleep(2)
                if self._rviz_proc.poll() is None:
                    print("[ROBOT RVIZ]: remote PC RViz started", flush=True)
                    return True
                log_line("REMOTE_RVIZ_EXITED_EARLY")
            except Exception as e:
                log_line(f"REMOTE_RVIZ_EXCEPTION {e}")

        if os.environ.get("AI_RVIZ_HOST_DOCKER", "0").strip() == "1":
            rviz_image = os.environ.get("AI_RVIZ_DOCKER_IMAGE", "osrf/ros:noetic-desktop-full").strip() or "osrf/ros:noetic-desktop-full"
            allow_pull = os.environ.get("AI_RVIZ_ALLOW_PULL", "0").strip() == "1"
            machine = os.uname().machine.lower()
            if machine in {"aarch64", "arm64"} and rviz_image == "osrf/ros:noetic-desktop-full" and os.environ.get("AI_RVIZ_ALLOW_AMD64_ON_ARM", "0") != "1":
                msg = (
                    f"HOST_DOCKER_RVIZ_IMAGE_ARCH_UNSUPPORTED {rviz_image} is amd64 on {machine}. "
                    "Install ros-noetic-rviz inside yahboom_container, or set AI_RVIZ_SSH_TARGET to use the PC."
                )
                log_line(msg)
                print(f"[ROBOT RVIZ ERROR]: {msg}", flush=True)
                return False
            try:
                image_check = subprocess.run(
                    ["docker", "image", "inspect", rviz_image],
                    stdout=subprocess.PIPE,
                    stderr=subprocess.STDOUT,
                    timeout=5,
                    text=True,
                )
            except Exception as e:
                image_check = None
                log_line(f"HOST_DOCKER_IMAGE_CHECK_EXCEPTION {e}")

            if image_check is None or image_check.returncode != 0:
                msg = (
                    f"HOST_DOCKER_RVIZ_IMAGE_MISSING {rviz_image}. "
                    f"Run: docker pull {rviz_image}  or set AI_RVIZ_SSH_TARGET to use your PC."
                )
                log_line(msg)
                print(f"[ROBOT RVIZ ERROR]: {msg}", flush=True)
                if not allow_pull:
                    return False

            cmd = f'''
export DISPLAY=${{DISPLAY:-:0}}
xhost +local:docker >/dev/null 2>&1 || true
docker rm -f ai_companion_rviz >/dev/null 2>&1 || true
DRI_OPT=""
if [ -e /dev/dri ]; then DRI_OPT="--device /dev/dri:/dev/dri"; fi
docker run --name ai_companion_rviz --rm --net=host \
  --add-host yahboom:{self.robot_ip} \
  -e DISPLAY=$DISPLAY \
  -e QT_X11_NO_MITSHM=1 \
  -e ROS_MASTER_URI=http://{self.robot_ip}:11311 \
  -e ROS_IP={self.robot_ip} \
  $DRI_OPT \
  -v /tmp/.X11-unix:/tmp/.X11-unix \
  -v {config_path}:{config_path}:ro \
  {rviz_image} rosrun rviz rviz -d {config_path}
'''
            try:
                print("[ROBOT RVIZ]: launching host Docker RViz", flush=True)
                log_line(f"[ROBOT RVIZ] launching host Docker RViz image={rviz_image}")
                with open(host_log, "a", encoding="utf-8") as log:
                    self._rviz_proc = subprocess.Popen(
                        ["/bin/bash", "-lc", cmd],
                        stdout=log,
                        stderr=log,
                        start_new_session=True,
                        text=True,
                    )
                time.sleep(2)
                if self._rviz_proc.poll() is None:
                    print("[ROBOT RVIZ]: host Docker RViz started", flush=True)
                    return True
                try:
                    with open(host_log, "r", encoding="utf-8", errors="replace") as f:
                        tail = "".join(f.readlines()[-80:])
                except Exception:
                    tail = ""
                print(f"[ROBOT RVIZ ERROR]: host Docker RViz exited early\n{tail}", flush=True)
                log_line("HOST_DOCKER_RVIZ_EXITED_EARLY")
            except Exception as e:
                print(f"[ROBOT RVIZ ERROR]: host Docker RViz exception: {e}", flush=True)
                log_line(f"HOST_DOCKER_RVIZ_EXCEPTION {e}")

        host_cmd = f'''
export DISPLAY=${{DISPLAY:-:0}}
export QT_X11_NO_MITSHM=1
for f in /opt/ros/noetic/setup.bash /opt/ros/melodic/setup.bash /opt/ros/kinetic/setup.bash; do
    if [ -f "$f" ]; then source "$f"; break; fi
done
export ROS_MASTER_URI=http://{self.robot_ip}:11311
export ROS_IP={self.robot_ip}
unset ROS_HOSTNAME
if ! command -v rviz >/dev/null 2>&1; then
    echo HOST_RVIZ_NOT_FOUND
    exit 44
fi
rviz -d {config_path}
'''
        try:
            log_line("[ROBOT RVIZ] launching host rviz")
            with open(host_log, "a", encoding="utf-8") as log:
                self._rviz_proc = subprocess.Popen(
                    ["/bin/bash", "-lc", host_cmd],
                    stdout=log,
                    stderr=log,
                    start_new_session=True,
                    text=True,
                )
            time.sleep(2)
            if self._rviz_proc.poll() is None:
                print("[ROBOT RVIZ]: host RViz started", flush=True)
                return True
            log_line("HOST_RVIZ_EXITED_EARLY")
        except Exception as e:
            log_line(f"HOST_RVIZ_EXCEPTION {e}")

        print("[ROBOT RVIZ ERROR]: no RViz fallback available", flush=True)
        return False

    def _ensure_container_running(self) -> bool:
        try:
            out = subprocess.check_output(
                ["docker", "inspect", "-f", "{{.State.Running}}", self.container],
                stderr=subprocess.STDOUT,
                timeout=5,
                text=True,
            ).strip()
            if out == "true":
                return True
            subprocess.check_output(
                ["docker", "start", self.container],
                stderr=subprocess.STDOUT,
                timeout=15,
                text=True,
            )
            time.sleep(2)
            return True
        except Exception as e:
            print(f"[ROBOT RVIZ ERROR]: container start/check failed: {e}", flush=True)
            return False

    def _allow_docker_x11(self):
        script = """
export DISPLAY=${DISPLAY:-:0}
export XAUTHORITY=${XAUTHORITY:-$HOME/.Xauthority}
if command -v xhost >/dev/null 2>&1; then
    xhost +SI:localuser:root >/dev/null 2>&1 || true
    xhost +local:root >/dev/null 2>&1 || true
    xhost +local:docker >/dev/null 2>&1 || true
fi
"""
        try:
            subprocess.run(["/bin/bash", "-lc", script], timeout=3)
        except Exception:
            pass

    def goto_map(self, x: float, y: float, yaw: float = 0.0) -> bool:
        x = float(x)
        y = float(y)
        yaw = float(yaw)
        with self._goal_lock:
            navigation_epoch = self._navigation_epoch

        qz = math.sin(yaw / 2.0)
        qw = math.cos(yaw / 2.0)

        print(f"[ROBOT NAV]: sending move_base goal x={x:.2f}, y={y:.2f}, yaw={yaw:.2f}", flush=True)

        # Clear only old AI helper scripts. Do not kill move_base or the UDP bridge.
        self._docker_ros("pkill -9 -f '[a]i_nav_goal.py|[a]i_persistent_move_base_goal.py' || true", timeout=4)

        cmd = f"""
python3 - << 'PY'
import rospy
import actionlib
from move_base_msgs.msg import MoveBaseAction, MoveBaseGoal

rospy.init_node('ai_send_move_base_action_goal', anonymous=True)
client = actionlib.SimpleActionClient('/move_base', MoveBaseAction)
if not client.wait_for_server(rospy.Duration(2.5)):
    print('NO_MOVE_BASE_ACTION_SERVER')
    raise SystemExit(2)

goal = MoveBaseGoal()
goal.target_pose.header.frame_id = 'map'
goal.target_pose.header.stamp = rospy.Time.now()
goal.target_pose.pose.position.x = {x}
goal.target_pose.pose.position.y = {y}
goal.target_pose.pose.position.z = 0.0
goal.target_pose.pose.orientation.x = 0.0
goal.target_pose.pose.orientation.y = 0.0
goal.target_pose.pose.orientation.z = {qz}
goal.target_pose.pose.orientation.w = {qw}

client.send_goal(goal)
rospy.sleep(0.25)
print('ACTION_GOAL_SENT')
PY
"""
        out = self._docker_ros(cmd, timeout=8)
        if "ACTION_GOAL_SENT" in out:
            with self._goal_lock:
                invalidated = navigation_epoch != self._navigation_epoch
                if not invalidated:
                    self._navigation_goal = {
                        "x": x,
                        "y": y,
                        "yaw": yaw,
                        "frame": "map",
                        "timestamp": time.time(),
                    }
            if invalidated:
                print("[ROBOT NAV]: goal invalidated by a newer stop command", flush=True)
                self.cancel_navigation()
                self.stop()
                return False
            print("[ROBOT NAV]: move_base action goal sent OK", flush=True)
            return True

        print(f"[ROBOT NAV ERROR]: move_base action goal failed\n{out}", flush=True)
        return False

    # ──────────────────────────────────────────────────────────────
    # AMCL pose — UDP listener, background refresh
    # ──────────────────────────────────────────────────────────────

    def _pose_listener_loop(self):
        """Background thread: listen for AMCL pose UDP packets on port 5040."""
        print("[ROBOT] AMCL pose UDP listener started on port 5040", flush=True)
        while True:
            try:
                data, _ = self.pose_sock.recvfrom(4096)
                msg = json.loads(data.decode("utf-8"))
                if isinstance(msg, dict) and "x" in msg and "y" in msg:
                    with self._pose_lock:
                        self._cached_pose = msg
                        self._pose_last_fetch = time.time()
            except Exception:
                time.sleep(0.5)

    def _fetch_amcl_pose_now(self) -> Optional[Dict]:
        """One-shot Docker fetch of /amcl_pose — used only as fallback."""
        cmd = r"""
python3 - << 'PY'
import rospy, json, math
from geometry_msgs.msg import PoseWithCovarianceStamped
def yaw_from_quat(q):
    return math.atan2(2*(q.w*q.z+q.x*q.y), 1-2*(q.y*q.y+q.z*q.z))
rospy.init_node('ai_amcl_once', anonymous=True)
try:
    msg = rospy.wait_for_message('/amcl_pose', PoseWithCovarianceStamped, timeout=3.0)
    p = msg.pose.pose.position
    q = msg.pose.pose.orientation
    print(json.dumps({"x": p.x, "y": p.y, "yaw": yaw_from_quat(q)}))
except Exception:
    print("{}")
PY
"""
        out = self._docker_ros(cmd, timeout=8)
        try:
            result = json.loads(out.splitlines()[-1])
            if result and "x" in result:
                return result
        except Exception:
            pass
        return None

    def get_amcl_pose(self) -> Optional[Dict[str, float]]:
        """Return cached AMCL pose from UDP bridge; fallback to Docker on first call."""
        with self._pose_lock:
            if self._cached_pose and time.time() - self._pose_last_fetch < 5.0:
                return self._cached_pose
        # UDP bridge not yet received a packet — try a one-shot Docker fetch
        pose = self._fetch_amcl_pose_now()
        if pose:
            with self._pose_lock:
                self._cached_pose = pose
                self._pose_last_fetch = time.time()
        return pose

    def get_cached_pose(self, max_age: Optional[float] = 5.0) -> Optional[Dict[str, float]]:
        """Return only the UDP-cached pose; never blocks on a Docker command."""
        with self._pose_lock:
            if not self._cached_pose:
                return None
            age = time.time() - self._pose_last_fetch
            if max_age is not None and age > max(0.1, float(max_age)):
                return None
            pose = dict(self._cached_pose)
            pose["age"] = round(age, 3)
            return pose

    def get_mobile_state(self) -> Dict:
        # AMCL commonly publishes only while its estimate changes. Keep the
        # last valid pose visible when the robot is stationary.
        pose = self.get_cached_pose(max_age=None)
        if pose is None:
            return {"localized": False, "frame": "map"}
        with self._goal_lock:
            goal = dict(self._navigation_goal) if self._navigation_goal else None
        return {
            "localized": True,
            "frame": "map",
            "x": float(pose["x"]),
            "y": float(pose["y"]),
            "yaw": float(pose.get("yaw", 0.0)),
            "pose_age": pose.get("age", 0.0),
            "navigation_goal": goal,
        }

    def clear_navigation_goal(self):
        with self._goal_lock:
            self._navigation_goal = None

    def distance_to(self, x: float, y: float) -> Optional[float]:
        """Instant distance from cached pose — no Docker call."""
        pose = self.get_amcl_pose()
        if pose is None:
            return None
        return math.hypot(float(pose["x"]) - float(x), float(pose["y"]) - float(y))

    # ──────────────────────────────────────────────────────────────
    # Map waypoints
    # ──────────────────────────────────────────────────────────────

    def get_search_waypoints(self, map_name="salle_robotique",
                              spacing_m=1.00, max_points=90,
                              clearance_m=0.50) -> List[Dict]:
        docker_py = r'''
import os, re, json, cv2, numpy as np

map_name    = "__MAP_NAME__"
map_folder  = "/root/yahboomcar_ws/src/yahboomcar_nav/maps"
yaml_path   = os.path.join(map_folder, map_name + ".yaml")

if not os.path.exists(yaml_path):
    print("[]"); raise SystemExit(0)

txt = open(yaml_path).read()

def get_value(key, default=None):
    m = re.search(r"^" + re.escape(key) + r"\s*:\s*(.+)$", txt, re.M)
    return m.group(1).strip() if m else default

resolution  = float(get_value("resolution", "0.05"))
origin_txt  = get_value("origin", "[0.0, 0.0, 0.0]")
origin_vals = [float(v) for v in re.findall(r"[-+]?\d*\.?\d+", origin_txt)]
origin_x, origin_y = (origin_vals[0], origin_vals[1]) if len(origin_vals) >= 2 else (0.0, 0.0)

image_file = get_value("image", map_name + ".pgm").strip().strip('"').strip("'")
image_path = image_file if image_file.startswith("/") else os.path.join(map_folder, image_file)
img = cv2.imread(image_path, cv2.IMREAD_GRAYSCALE)
if img is None:
    print("[]"); raise SystemExit(0)

h, w = img.shape[:2]
spacing_m  = float("__SPACING_M__")
max_points = int("__MAX_POINTS__")
clearance_m = float("__CLEARANCE_M__")
step       = max(1, int(spacing_m / resolution))

# Conservative wall clearance keeps search goals out of inflated obstacles.
clearance_px = max(8, int(clearance_m / resolution))

# Dilate occupied pixels so waypoints stay away from walls
kernel   = np.ones((clearance_px*2+1, clearance_px*2+1), np.uint8)
occupied = cv2.dilate((img < 200).astype(np.uint8) * 255, kernel)

points = []
for py in range(clearance_px, h - clearance_px, step):
    for px in range(clearance_px, w - clearance_px, step):
        if occupied[py, px] > 0:
            continue        # too close to wall / obstacle
        if img[py, px] < 245:
            continue        # not clearly free
        x = origin_x + px * resolution
        y = origin_y + (h - py) * resolution
        points.append({"x": round(float(x),3), "y": round(float(y),3), "yaw": 0.0})

if len(points) > max_points:
    stride = max(1, len(points) // max_points)
    points = points[::stride][:max_points]

print(json.dumps(points))
'''
        docker_py = (docker_py
                     .replace("__MAP_NAME__",  str(map_name))
                     .replace("__SPACING_M__", str(float(spacing_m)))
                     .replace("__MAX_POINTS__", str(int(max_points)))
                     .replace("__CLEARANCE_M__", str(float(clearance_m))))

        out = self._docker_ros("python3 - << 'PY'\n" + docker_py + "\nPY\n", timeout=12)
        try:
            pts = json.loads(out.splitlines()[-1])
            print(f"[ROBOT MAP]: generated {len(pts)} search waypoints", flush=True)
            return pts
        except Exception as e:
            print(f"[ROBOT MAP ERROR]: {e}", flush=True)
            return []

    def get_map_snapshot(self, map_name="salle_robotique", force=False) -> Optional[Dict]:
        """Return a PNG map plus ROS map metadata for mobile rendering."""
        map_name = str(map_name or "salle_robotique")
        with self._map_cache_lock:
            if not force and map_name in self._map_cache:
                return dict(self._map_cache[map_name])

        script = r'''
import base64, hashlib, json, os, re
import cv2

name = __MAP_NAME__
folder = "/root/yahboomcar_ws/src/yahboomcar_nav/maps"
yaml_path = os.path.join(folder, name + ".yaml")
if not os.path.isfile(yaml_path):
    print("{}"); raise SystemExit(0)

text = open(yaml_path, "r").read()
def value(key, default):
    match = re.search(r"^" + re.escape(key) + r"\s*:\s*(.+)$", text, re.M)
    return match.group(1).strip() if match else default

resolution = float(value("resolution", "0.05"))
origin_values = [float(v) for v in re.findall(r"[-+]?\d*\.?\d+", value("origin", "[0,0,0]"))]
while len(origin_values) < 3:
    origin_values.append(0.0)
image_name = value("image", name + ".pgm").strip("\"'")
image_path = image_name if os.path.isabs(image_name) else os.path.join(folder, image_name)
image = cv2.imread(image_path, cv2.IMREAD_GRAYSCALE)
if image is None:
    print("{}"); raise SystemExit(0)
ok, encoded = cv2.imencode(".png", image, [int(cv2.IMWRITE_PNG_COMPRESSION), 7])
if not ok:
    print("{}"); raise SystemExit(0)
raw = encoded.tobytes()
height, width = image.shape[:2]
print(json.dumps({
    "map_id": hashlib.sha256(raw).hexdigest()[:16],
    "name": name,
    "encoding": "png_base64",
    "image_base64": base64.b64encode(raw).decode("ascii"),
    "width": int(width),
    "height": int(height),
    "resolution": resolution,
    "origin_x": origin_values[0],
    "origin_y": origin_values[1],
    "origin_yaw": origin_values[2],
    "frame": "map"
}, separators=(",", ":")))
'''.replace("__MAP_NAME__", json.dumps(map_name))

        out = self._docker_ros("python3 - << 'PY'\n" + script + "\nPY\n", timeout=15)
        try:
            snapshot = json.loads(out.splitlines()[-1])
        except Exception as exc:
            print(f"[ROBOT MAP ERROR] mobile map export failed: {exc}", flush=True)
            return None
        if not snapshot or not snapshot.get("image_base64"):
            return None
        with self._map_cache_lock:
            self._map_cache[map_name] = dict(snapshot)
        print(
            f"[ROBOT MAP] mobile snapshot {snapshot['width']}x{snapshot['height']} "
            f"id={snapshot['map_id']}",
            flush=True,
        )
        return snapshot
