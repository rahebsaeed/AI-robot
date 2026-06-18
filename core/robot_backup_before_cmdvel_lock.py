import socket
import json
import subprocess
import threading
import time
import math
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
                shell=True,
                text=True
            ).strip()

            if ip and ip not in self.targets:
                self.targets.append(ip)

            self.robot_ip = ip if ip else "192.168.50.196"

        except Exception:
            self.robot_ip = "192.168.50.196"

        self._move_thread = None
        self._stop_event = threading.Event()
        self._lock = threading.Lock()

        print(f"[ROBOT] UDP cmd_vel targets={self.targets}, port={self.port}", flush=True)
        print(f"[ROBOT] move_base navigation active through Docker: {self.container}", flush=True)

    # --------------------------------------------------
    # Docker ROS helper
    # --------------------------------------------------

    def _docker_ros(self, command: str, timeout: int = 20) -> str:
        ros_prefix = f"""
export ROBOT_TYPE=X3plus
export LASER_TYPE=4ROS
export ROS_MASTER_URI=http://{self.robot_ip}:11311
export ROS_IP={self.robot_ip}
unset ROS_HOSTNAME
source /root/yahboomcar_ws/devel/setup.bash
"""
        full_cmd = ros_prefix + "\n" + command

        try:
            out = subprocess.check_output(
                ["docker", "exec", self.container, "/bin/bash", "-lc", full_cmd],
                stderr=subprocess.STDOUT,
                timeout=timeout,
                text=True
            )
            return out.strip()
        except subprocess.CalledProcessError as e:
            print("[ROBOT DOCKER ERROR]", e.output, flush=True)
            return ""
        except subprocess.TimeoutExpired:
            print("[ROBOT DOCKER ERROR] command timed out", flush=True)
            return ""

    # --------------------------------------------------
    # Manual UDP movement
    # --------------------------------------------------

    def _send_once(self, payload):
        data = json.dumps(payload).encode("utf-8")

        for host in self.targets:
            try:
                self.sock.sendto(data, (host, self.port))
                print(f"[ROBOT UDP SENT]: {host}:{self.port} {payload}", flush=True)
            except Exception as e:
                print(f"[ROBOT UDP ERROR] target={host}:{self.port} error={e}", flush=True)

    def _send_move(self, direction, speed):
        self._send_once({
            "type": "move",
            "direction": direction,
            "speed": float(speed)
        })

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

        valid = ["forward", "backward", "left", "right", "stop"]

        if direction not in valid:
            direction = "stop"
            speed = 0.0

        if direction == "stop":
            speed = 0.0

        if duration is None:
            if direction == "stop":
                duration = 0.0
            elif direction in ["left", "right"]:
                duration = 5.0
            elif direction == "forward":
                duration = 10.0
            else:
                duration = 6.0

        print(f"[ROBOT UDP COMMAND]: direction={direction}, speed={speed}, duration={duration}", flush=True)

        with self._lock:
            self._stop_event.set()

            if self._move_thread is not None and self._move_thread.is_alive():
                self._move_thread.join(timeout=0.4)

            self._stop_event.clear()

            if direction == "stop" or duration <= 0:
                self._send_move("stop", 0.0)
                return

            self._move_thread = threading.Thread(
                target=self._continuous_sender,
                args=(direction, speed, duration),
                daemon=True
            )
            self._move_thread.start()

    def stop(self):
        with self._lock:
            self._stop_event.set()

        self._send_move("stop", 0.0)

    def control_arm(self, command="home"):
        command = str(command).strip().lower()

        if command == "searching":
            print("[ROBOT ARM]: searching mode only; map-search task controls movement", flush=True)

        payload = {
            "type": "arm",
            "command": command
        }

        print(f"[ROBOT ARM UDP]: {payload}", flush=True)
        self._send_once(payload)

    # --------------------------------------------------
    # move_base navigation
    # --------------------------------------------------

    def cancel_navigation(self):
        print("[ROBOT NAV]: cancel move_base goal", flush=True)

        self._docker_ros(
            """
pkill -9 -f '[a]i_persistent_move_base_goal.py' || true

python3 - << 'PY'
import rospy
from actionlib_msgs.msg import GoalID

rospy.init_node('ai_cancel_move_base', anonymous=True)
pub = rospy.Publisher('/move_base/cancel', GoalID, queue_size=1)

rospy.sleep(0.5)
pub.publish(GoalID())
rospy.sleep(0.3)
print("CANCEL_SENT")
PY
""",
            timeout=6
        )

    def goto_map(self, x: float, y: float, yaw: float = 0.0) -> bool:
        """
        Start a persistent move_base action client inside Docker.

        This keeps the navigation goal alive, similar to a professional ROS node,
        instead of sending one short request and exiting.
        """

        x = float(x)
        y = float(y)
        yaw = float(yaw)

        qz = math.sin(yaw / 2.0)
        qw = math.cos(yaw / 2.0)

        print(f"[ROBOT NAV]: starting persistent move_base goal x={x:.2f}, y={y:.2f}, yaw={yaw:.2f}", flush=True)

        # Kill only previous AI navigation client, not move_base itself.
        self._docker_ros("pkill -9 -f '[a]i_persistent_move_base_goal.py' || true", timeout=4)

        script = f"""
cat > /tmp/ai_persistent_move_base_goal.py << 'PY'
#!/usr/bin/env python3
import rospy
import actionlib
import math
import time
from move_base_msgs.msg import MoveBaseAction, MoveBaseGoal
from geometry_msgs.msg import Twist

goal_x = {x}
goal_y = {y}
goal_qz = {qz}
goal_qw = {qw}

last_cmd_time = time.time()
last_nonzero_cmd_time = 0.0

def cmd_cb(msg):
    global last_cmd_time, last_nonzero_cmd_time
    last_cmd_time = time.time()
    if abs(msg.linear.x) > 0.01 or abs(msg.angular.z) > 0.01:
        last_nonzero_cmd_time = time.time()

rospy.init_node('ai_persistent_move_base_goal', anonymous=False)

rospy.Subscriber('/cmd_vel', Twist, cmd_cb, queue_size=10)

client = actionlib.SimpleActionClient('/move_base', MoveBaseAction)

print('[AI NAV NODE] waiting for move_base action server', flush=True)
if not client.wait_for_server(rospy.Duration(10.0)):
    print('[AI NAV NODE] ERROR move_base action server not available', flush=True)
    raise SystemExit(1)

goal = MoveBaseGoal()
goal.target_pose.header.frame_id = 'map'
goal.target_pose.header.stamp = rospy.Time.now()
goal.target_pose.pose.position.x = goal_x
goal.target_pose.pose.position.y = goal_y
goal.target_pose.pose.position.z = 0.0
goal.target_pose.pose.orientation.x = 0.0
goal.target_pose.pose.orientation.y = 0.0
goal.target_pose.pose.orientation.z = goal_qz
goal.target_pose.pose.orientation.w = goal_qw

client.send_goal(goal)
print('[AI NAV NODE] GOAL_SENT x=%.3f y=%.3f' % (goal_x, goal_y), flush=True)

rate = rospy.Rate(2)

while not rospy.is_shutdown():
    state = client.get_state()
    txt = client.get_goal_status_text()

    now = time.time()
    nonzero_age = now - last_nonzero_cmd_time if last_nonzero_cmd_time > 0 else -1

    print('[AI NAV NODE] state=%s status=%s nonzero_cmd_age=%.1f' % (state, txt, nonzero_age), flush=True)

    # actionlib states:
    # 3 SUCCEEDED, 4 ABORTED, 5 REJECTED, 2 PREEMPTED
    if state in [2, 3, 4, 5, 8]:
        print('[AI NAV NODE] FINISHED state=%s status=%s' % (state, txt), flush=True)
        break

    rate.sleep()
PY

chmod +x /tmp/ai_persistent_move_base_goal.py
nohup python3 /tmp/ai_persistent_move_base_goal.py > /tmp/ai_persistent_move_base_goal.log 2>&1 &
sleep 1
tail -20 /tmp/ai_persistent_move_base_goal.log
"""

        out = self._docker_ros(script, timeout=8)

        if "GOAL_SENT" in out:
            print("[ROBOT NAV]: persistent action goal started successfully", flush=True)
            return True

        # Sometimes tail may run before GOAL_SENT appears. Check the log once.
        out2 = self._docker_ros("tail -40 /tmp/ai_persistent_move_base_goal.log 2>/dev/null || true", timeout=4)

        if "GOAL_SENT" in out2:
            print("[ROBOT NAV]: persistent action goal started successfully", flush=True)
            return True

        print(f"[ROBOT NAV ERROR]: persistent goal failed. Output: {out}\n{out2}", flush=True)
        return False

    def get_amcl_pose(self) -> Optional[Dict[str, float]]:
        cmd = """
python3 - << 'PY'
import rospy
import json
import math
from geometry_msgs.msg import PoseWithCovarianceStamped

def yaw_from_quat(q):
    siny_cosp = 2.0 * (q.w * q.z + q.x * q.y)
    cosy_cosp = 1.0 - 2.0 * (q.y * q.y + q.z * q.z)
    return math.atan2(siny_cosp, cosy_cosp)

rospy.init_node('ai_read_amcl_pose', anonymous=True)
msg = rospy.wait_for_message('/amcl_pose', PoseWithCovarianceStamped, timeout=3.0)

p = msg.pose.pose.position
q = msg.pose.pose.orientation

print(json.dumps({
    "x": p.x,
    "y": p.y,
    "yaw": yaw_from_quat(q)
}))
PY
"""
        out = self._docker_ros(cmd, timeout=7)

        try:
            return json.loads(out.splitlines()[-1])
        except Exception:
            print("[ROBOT NAV]: could not read AMCL pose", flush=True)
            print(f"[ROBOT NAV RAW]: {out}", flush=True)
            return None

    def distance_to(self, x: float, y: float) -> Optional[float]:
        pose = self.get_amcl_pose()

        if pose is None:
            return None

        return math.hypot(float(pose["x"]) - float(x), float(pose["y"]) - float(y))

    def get_search_waypoints(self, map_name="salle_robotique", spacing_m=0.9, max_points=120) -> List[Dict[str, float]]:
        docker_py = r'''
import os
import re
import json
import cv2
import numpy as np

map_name = "__MAP_NAME__"
map_folder = "/root/yahboomcar_ws/src/yahboomcar_nav/maps"
yaml_path = os.path.join(map_folder, map_name + ".yaml")

if not os.path.exists(yaml_path):
    print("[]")
    raise SystemExit(0)

txt = open(yaml_path).read()

def get_value(key, default=None):
    m = re.search(r"^" + re.escape(key) + r"\s*:\s*(.+)$", txt, re.M)
    return m.group(1).strip() if m else default

image_file = get_value("image", map_name + ".pgm").strip().strip('"').strip("'")
resolution = float(get_value("resolution", "0.05"))

origin_txt = get_value("origin", "[0.0, 0.0, 0.0]")
origin_vals = [float(v) for v in re.findall(r"[-+]?\d*\.\d+|[-+]?\d+", origin_txt)]

if len(origin_vals) < 2:
    origin_x, origin_y = 0.0, 0.0
else:
    origin_x, origin_y = origin_vals[0], origin_vals[1]

if not image_file.startswith("/"):
    image_path = os.path.join(map_folder, image_file)
else:
    image_path = image_file

img = cv2.imread(image_path, cv2.IMREAD_GRAYSCALE)

if img is None:
    print("[]")
    raise SystemExit(0)

h, w = img.shape[:2]

spacing_m = float("__SPACING_M__")
max_points = int("__MAX_POINTS__")

step = max(1, int(spacing_m / resolution))

clearance_m = 0.35
clearance_px = max(2, int(clearance_m / resolution))

points = []

for py in range(clearance_px, h - clearance_px, step):
    for px in range(clearance_px, w - clearance_px, step):
        patch = img[py-clearance_px:py+clearance_px+1, px-clearance_px:px+clearance_px+1]

        if patch.size == 0:
            continue

        # White/free only, with clearance.
        if np.min(patch) < 245:
            continue

        x = origin_x + px * resolution
        y = origin_y + (h - py) * resolution

        points.append({
            "x": round(float(x), 3),
            "y": round(float(y), 3),
            "yaw": 0.0
        })

if len(points) > max_points:
    stride = max(1, len(points) // max_points)
    points = points[::stride][:max_points]

print(json.dumps(points))
'''

        docker_py = docker_py.replace("__MAP_NAME__", str(map_name))
        docker_py = docker_py.replace("__SPACING_M__", str(float(spacing_m)))
        docker_py = docker_py.replace("__MAX_POINTS__", str(int(max_points)))

        cmd = "python3 - << 'PY'\n" + docker_py + "\nPY\n"

        out = self._docker_ros(cmd, timeout=12)

        try:
            points = json.loads(out.splitlines()[-1])
            print(f"[ROBOT MAP]: generated {len(points)} search waypoints", flush=True)
            return points
        except Exception as e:
            print(f"[ROBOT MAP ERROR]: could not parse waypoints: {e}", flush=True)
            print(f"[ROBOT MAP RAW OUTPUT]: {out}", flush=True)
            return []
