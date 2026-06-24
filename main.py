"""
main.py — Fixed main loop for Rosmaster X3 PLUS AI companion
=============================================================
Key fixes:
 1. perceptions.speak() now receives ONLY the speech string, not a dict
 2. Face bridge sends structured JSON so the interface can show YOLO/thinking state
 3. Vision data shown on face on every cycle, not just on search
 4. Search target validated BEFORE speaking "I will search for..."
 5. LLM response is printed as a clean string, not a dict repr
"""

import os, sys, json, re, cv2, time, signal, subprocess, socket, threading, math
from core.brain import Brain
from core.perceptions import Perceptions
from core.robot import Robot
from core.search_tasks import (
    MapSearchTask, is_search_request, parse_search_target,
    resolve_yolo_name, is_searchable_target,
)
from core.face_bridge import FaceBridge
from core.places_memory import PlacesMemory
from core.command_bus import CommandBus, MicrophoneCommandProducer
from core.mobile_control import MobileTeleopController
from core.mobile_gateway import MobileGateway

OBSTACLE_STOP_DISTANCE = 0.45
OBSTACLE_SLOW_DISTANCE = 0.80
DEFAULT_FORWARD_SPEED  = 0.25
DEFAULT_BACKWARD_SPEED = 0.20
DEFAULT_TURN_SPEED     = 0.22
MAX_SAFE_SPEED         = 0.35
PERSON_FORWARD_SPEED   = 0.15

# ──────────────────────────────────────────────────────────────────
# Face process
# ──────────────────────────────────────────────────────────────────

def start_robot_face():
    project_dir = os.path.dirname(os.path.abspath(__file__))
    face_file   = os.path.join(project_dir, "robot_face.py")
    log_file    = os.path.join(project_dir, "face_interface.log")

    if not os.path.exists(face_file):
        print(f"[FACE ERROR] robot_face.py not found at: {face_file}", flush=True)
        return None

    print("[FACE] Starting robot face interface...", flush=True)
    try:
        env = os.environ.copy()
        env["QT_QPA_PLATFORM"]           = "xcb"
        env["DISPLAY"]                    = env.get("DISPLAY", ":0")
        env["QT_PLUGIN_PATH"]             = "/usr/lib/aarch64-linux-gnu/qt5/plugins"
        env["QT_QPA_PLATFORM_PLUGIN_PATH"]= "/usr/lib/aarch64-linux-gnu/qt5/plugins/platforms"
        xauth = os.path.expanduser("~/.Xauthority")
        if os.path.exists(xauth):
            env["XAUTHORITY"] = xauth

        log  = open(log_file, "w")
        proc = subprocess.Popen(
            [sys.executable, face_file],
            cwd=project_dir, stdout=log, stderr=log,
            env=env, start_new_session=True
        )
        time.sleep(3)
        if proc.poll() is not None:
            print(f"[FACE ERROR] crashed — check {log_file}", flush=True)
            return None
        print("[FACE] Interface running.", flush=True)
        return proc
    except Exception as e:
        print(f"[FACE ERROR] {e}", flush=True)
        return None

def stop_robot_face(proc):
    if proc is None:
        return
    try:
        proc.terminate()
        proc.wait(timeout=3)
    except Exception:
        try:
            os.killpg(os.getpgid(proc.pid), signal.SIGTERM)
        except Exception:
            pass

# ──────────────────────────────────────────────────────────────────
# Face bridge helpers — send structured data
# ──────────────────────────────────────────────────────────────────

def face_show_thinking(face: FaceBridge, user_text: str):
    face.send_emotion("thinking")
    face.send_status({
        "mode": "thinking",
        "phase": "processing",
        "message": f"Thinking about: {user_text}",
        "can_talk": False,
    })
    face.send_message(f"Thinking about: {user_text}")

def face_show_response(face: FaceBridge, user_text: str,
                       ai_speech: str, ai_thought: str,
                       vision_desc: str, objects: list,
                       lidar: float | None, latency: float):
    """Send a rich status update to the face interface."""
    obj_str = ", ".join(
        f"{o.get('name','?')}({o.get('position','?')})"
        for o in objects[:4]
    ) if objects else "none"

    lidar_str = f"{lidar:.2f} m" if lidar is not None else "N/A"

    msg = (
        f"You: {user_text}\n"
        f"AI: {ai_speech}\n"
        f"Thought: {ai_thought}\n"
        f"Objects: {obj_str}\n"
        f"Lidar: {lidar_str}  Time: {latency:.1f}s"
    )
    emotion = face.detect_emotion(ai_speech)
    face.send_emotion(emotion)
    face.send_status({
        "mode": "ready",
        "phase": "listening",
        "message": "Ready. You can speak now.",
        "objects": objects[:4],
        "can_talk": True,
    })
    face.send_message(msg)

def face_show_search(face: FaceBridge, target: str, phase: str,
                     wp_idx: int = 0, wp_total: int = 0,
                     objects: list = None, found: bool = False,
                     searched_count: int = 0, message: str = "",
                     can_talk: bool = False):
    """Update face during search without leaving the screen blank."""
    if phase in {"cancelled", "stopped"}:
        mode, emotion = "stopped", "neutral"
    else:
        mode = "found" if found else ("not_found" if phase == "not_found" else "searching")
        emotion = "happy" if found else ("sad" if mode == "not_found" else "thinking")
    face.send_emotion(emotion)
    face.send_status({
        "mode": mode,
        "target": target,
        "phase": phase,
        "waypoint_index": wp_idx,
        "waypoint_total": wp_total,
        "searched_count": searched_count,
        "objects": (objects or [])[:4],
        "found": found,
        "message": message or phase,
        "can_talk": can_talk,
    })


def face_show_search_status(face: FaceBridge, status: dict):
    if not isinstance(status, dict):
        return
    phase = str(status.get("phase", "searching"))
    found = bool(status.get("found", False))
    if found or phase == "found":
        face.send_emotion("happy")
    elif phase in {"not_found", "rejected", "navigation_failed"}:
        face.send_emotion("sad")
    elif phase in {"cancelled", "stopped"}:
        face.send_emotion("neutral")
    elif phase == "recovery":
        face.send_emotion("fear")
    else:
        face.send_emotion("thinking")
    face.send_status(status)


def deliver_response(perceptions, face, mobile_gateway, envelope,
                     user_text: str, speech: str, face_message: bool = True,
                     status: str = "completed", **extra):
    """Deliver one final response consistently to robot audio, face, and Android."""
    if face_message:
        face.send_message(f"You: {user_text}\nAI: {speech}")
    perceptions.speak(speech)
    if mobile_gateway is not None:
        mobile_gateway.publish_response(
            envelope.request_id,
            speech,
            envelope.source,
            status=status,
            **extra,
        )


class UiCommandListener:
    def __init__(self, robot: Robot, search_task: MapSearchTask,
                 face: FaceBridge, perceptions: Perceptions,
                 stop_search_callback=None, mic_control_callback=None,
                 host="127.0.0.1", port=5006):
        self.robot = robot
        self.search_task = search_task
        self.face = face
        self.perceptions = perceptions
        self.stop_search_callback = stop_search_callback
        self.mic_control_callback = mic_control_callback
        self.host = host
        self.port = port
        self._thread = threading.Thread(target=self._run, daemon=True)

    def start(self):
        self._thread.start()

    def _run(self):
        sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        try:
            sock.bind((self.host, self.port))
            print(f"[UI COMMAND] listener on {self.host}:{self.port}", flush=True)
        except Exception as e:
            print(f"[UI COMMAND ERROR] bind failed: {e}", flush=True)
            return

        while True:
            try:
                data, _ = sock.recvfrom(4096)
                msg = json.loads(data.decode("utf-8"))
                command = str(msg.get("type", "")).strip().lower()
                if command == "stop_search":
                    self._handle_stop_search()
                elif command == "show_rviz":
                    self._handle_show_rviz()
                elif command == "mic_off":
                    self._set_microphone(False)
                elif command == "mic_on":
                    self._set_microphone(True)
            except Exception as e:
                print(f"[UI COMMAND ERROR] {e}", flush=True)

    def _set_microphone(self, enabled: bool):
        if self.mic_control_callback is not None:
            self.mic_control_callback(bool(enabled), "", "face_ui")
            return
        self.perceptions.set_mute(not bool(enabled))

    def _handle_stop_search(self):
        print("[UI COMMAND] stop_search", flush=True)
        if self.stop_search_callback is not None:
            self.stop_search_callback("", "face_ui")
            return
        try:
            self.search_task.request_cancel()
        except Exception:
            pass

        self.face.send_status({
            "mode": "stopped",
            "phase": "cancelled",
            "message": "Stopping search.",
            "can_talk": False,
        })
        threading.Thread(target=self._stop_robot_now, daemon=True).start()

    def _stop_robot_now(self):
        try:
            self.robot.cancel_navigation()
            self.robot.stop()
        except Exception as e:
            print(f"[UI COMMAND STOP ERROR] {e}", flush=True)

    def _handle_show_rviz(self):
        print("[UI COMMAND] show_rviz", flush=True)
        self.face.send_status({
            "mode": "ready",
            "phase": "rviz",
            "message": "Opening RViz.",
            "can_talk": True,
        })
        threading.Thread(target=self._launch_rviz, daemon=True).start()

    def _launch_rviz(self):
        try:
            ok = self.robot.launch_rviz()
            self.face.send_status({
                "mode": "ready" if ok else "not_found",
                "phase": "rviz",
                "message": "RViz opened." if ok else "Could not open RViz. Check /tmp/ai_companion.log and /tmp/ai_rviz.log.",
                "can_talk": True,
            })
        except Exception as e:
            print(f"[UI COMMAND RVIZ ERROR] {e}", flush=True)

# ──────────────────────────────────────────────────────────────────
# Helper functions
# ──────────────────────────────────────────────────────────────────

def parse_save_place(command: str):
    command = command.lower().strip()
    match = re.search(r"\b(?:save|record|memorize)\s+(?:this\s+)?(?:place|location|here|position)\s+as\s+([a-zA-Z0-9_\s-]+)", command)
    if match:
        return match.group(1).strip()
    return None

def parse_delete_place(command: str):
    command = command.lower().strip()
    match = re.search(r"\b(?:delete|remove|forget)\s+(?:the\s+)?(?:place|location|position)\s+([a-zA-Z0-9_\s-]+)", command)
    if match:
        return match.group(1).strip()
    return None

def parse_rename_place(command: str):
    command = command.lower().strip()
    match = re.search(r"\b(?:rename|change)\s+(?:the\s+)?(?:place|location|position)\s+([a-zA-Z0-9_\s-]+)\s+to\s+([a-zA-Z0-9_\s-]+)", command)
    if match:
        return match.group(1).strip(), match.group(2).strip()
    return None

def is_list_places_request(command: str) -> bool:
    t = command.lower().strip()
    return any(p in t for p in [
        "what places do you know",
        "list the places",
        "list saved places",
        "what locations do you remember",
        "show saved places",
    ])

def is_places_count_request(command: str) -> bool:
    t = command.lower().strip()
    return any(p in t for p in [
        "how many places",
        "how many saved places",
        "how many locations",
        "how many locations do you remember",
        "how many places do you remember",
        "how many places are in your memory",
        "count the places",
        "count saved places",
    ])

def is_map_name_request(command: str) -> bool:
    t = normalize_user_text(command)
    if "map" not in t:
        return False
    return bool(
        re.search(r"\bwhat'?s\s+(?:the\s+)?(?:name\s+of\s+)?(?:this\s+|current\s+)?map\b", t)
        or re.search(r"\bwhat\s+is\s+(?:the\s+)?(?:name\s+of\s+)?(?:this\s+|current\s+)?map\b", t)
        or re.search(r"\bwhich\s+(?:is\s+)?(?:this\s+|current\s+)?map\b", t)
        or re.fullmatch(r"(?:this\s+|current\s+)?map\s+name", t)
        or re.fullmatch(r"(?:which|what)\s+(?:this\s+)?map", t)
    )

def describe_map_identity(map_name: str, places_memory: PlacesMemory) -> str:
    names = places_memory.list_places(map_name)
    if not names:
        return f"The current map is named {map_name}. I do not have saved places on it yet."
    return (
        f"The current map is named {map_name}. "
        f"I know {len(names)} saved place{'s' if len(names) != 1 else ''} on it: {', '.join(names)}."
    )

def is_current_place_request(command: str) -> bool:
    t = normalize_user_text(command)
    phrases = [
        "define your place",
        "define this place",
        "define your position",
        "what is this place",
        "what place is this",
        "which place is this",
        "which place are you",
        "what is your place",
        "what is your current place",
        "do you know this position",
        "you know this position",
        "know this position where you are",
        "is this a saved place",
        "am i at a saved place",
    ]
    return any(p in t for p in phrases)

def current_pose_for_facts(robot: Robot):
    state = robot.get_mobile_state()
    if state.get("localized"):
        return {
            "x": float(state.get("x", 0.0)),
            "y": float(state.get("y", 0.0)),
            "yaw": float(state.get("yaw", 0.0)),
            "age": float(state.get("pose_age", 0.0)),
        }
    try:
        pose = robot.get_amcl_pose()
    except Exception:
        pose = None
    if pose:
        return {
            "x": float(pose.get("x", 0.0)),
            "y": float(pose.get("y", 0.0)),
            "yaw": float(pose.get("yaw", 0.0)),
            "age": 0.0,
        }
    return None

def nearest_saved_place(map_name: str, places_memory: PlacesMemory, pose: dict):
    best = None
    for name in places_memory.list_places(map_name):
        place = places_memory.get_place(map_name, name)
        if not place:
            continue
        dist = math.hypot(float(place["x"]) - pose["x"], float(place["y"]) - pose["y"])
        if best is None or dist < best[2]:
            best = (name, place, dist)
    return best

def build_place_navigation_goals(robot: Robot, map_name: str, place: dict,
                                 max_goals: int | None = None) -> list:
    target_x = float(place["x"])
    target_y = float(place["y"])
    target_yaw = float(place.get("yaw", 0.0))
    if max_goals is None:
        max_goals = int(os.environ.get("AI_PLACE_APPROACH_GOALS", "8"))
    max_goals = max(1, int(max_goals))

    goals = []

    def add_goal(goal: dict, default_kind: str = "approach"):
        try:
            x = float(goal["x"])
            y = float(goal["y"])
        except Exception:
            return
        yaw = float(goal.get("yaw", math.atan2(target_y - y, target_x - x)))
        kind = str(goal.get("kind", default_kind))
        for existing in goals:
            if math.hypot(existing["x"] - x, existing["y"] - y) < 0.18:
                return
        goals.append({
            "x": x,
            "y": y,
            "yaw": yaw,
            "kind": kind,
            "radius": float(goal.get("radius", 0.0) or 0.0),
            "line_clear": bool(goal.get("line_clear", False)),
        })

    add_goal({"x": target_x, "y": target_y, "yaw": target_yaw, "kind": "saved"}, "saved")

    if hasattr(robot, "get_waypoints_near") and len(goals) < max_goals:
        try:
            clearance = float(os.environ.get("AI_PLACE_APPROACH_CLEARANCE_M", "0.45"))
            candidates = robot.get_waypoints_near(
                map_name, target_x, target_y, target_yaw,
                clearance_m=clearance,
                max_points=max_goals * 2,
            )
            for candidate in candidates:
                add_goal(candidate, "approach")
                if len(goals) >= max_goals:
                    break
        except Exception as e:
            print(f"[PLACES NAV] Could not generate map approach goals: {e}", flush=True)

    return goals[:max_goals]

def describe_current_place(robot: Robot, map_name: str, places_memory: PlacesMemory) -> str:
    pose = current_pose_for_facts(robot)
    names = places_memory.list_places(map_name)
    if pose is None:
        if names:
            return (
                f"I know the map {map_name}, with saved places: {', '.join(names)}. "
                "My AMCL pose is not ready yet, so I cannot match this exact position."
            )
        return f"I know the map {map_name}, but my AMCL pose is not ready yet."

    if not names:
        return (
            f"I am on map {map_name} at x={pose['x']:.2f}, y={pose['y']:.2f}. "
            "There are no saved places on this map yet."
        )

    nearest = nearest_saved_place(map_name, places_memory, pose)
    if nearest is None:
        return (
            f"I am on map {map_name} at x={pose['x']:.2f}, y={pose['y']:.2f}. "
            "There are no valid saved places to compare with."
        )
    name, _place, dist = nearest
    match_radius = float(os.environ.get("AI_PLACE_MATCH_RADIUS", "0.80"))
    if dist <= match_radius:
        return (
            f"This position is {name} on map {map_name}. "
            f"My pose is x={pose['x']:.2f}, y={pose['y']:.2f}, about {dist:.2f} m from that saved place."
        )
    return (
        f"I am on map {map_name} at x={pose['x']:.2f}, y={pose['y']:.2f}. "
        f"The closest saved place is {name}, about {dist:.2f} m away, so I am not exactly at a saved place."
    )

def normalize_user_text(command: str) -> str:
    t = re.sub(r"[?.!,]", " ", str(command).lower())
    return re.sub(r"\s+", " ", t).strip()

def is_question_like(command: str) -> bool:
    t = normalize_user_text(command)
    return bool(
        re.search(r"\b(what|where|who|when|why|how|which|can|could|do|does|did|is|are)\b", t)
        or t.endswith("?")
    )

def is_robot_pose_request(command: str) -> bool:
    t = normalize_user_text(command)
    self_terms = r"(?:you|u|yourself|your|robot|the robot)"
    return bool(
        re.search(rf"\bwhere\s+(?:are|r|is)\s+{self_terms}\b", t)
        or re.search(r"\bwhat\s+is\s+(?:your|the\s+robot(?:'s)?)\s+(?:position|location|pose)\b", t)
        or re.search(r"\b(?:tell|show|give)\s+me\s+(?:your|the\s+robot(?:'s)?|robot)\s+(?:position|location|pose)\b", t)
        or re.fullmatch(r"(?:robot\s+)?(?:position|location|pose)", t)
    )

def describe_robot_pose(robot: Robot, map_name: str) -> str:
    pose = current_pose_for_facts(robot)
    state = robot.get_mobile_state()
    if pose is None:
        return "I do not have a valid AMCL position yet."
    x = float(pose.get("x", 0.0))
    y = float(pose.get("y", 0.0))
    yaw = float(pose.get("yaw", 0.0))
    heading = int(round(math.degrees(yaw)))
    pose_age = float(pose.get("age", 0.0))
    speech = (
        f"I am on map {map_name} at x={x:.2f}, y={y:.2f}, "
        f"heading {heading} degrees."
    )
    if pose_age > 60.0:
        speech += f" This is my last known pose from {pose_age:.0f} seconds ago."
    goal = state.get("navigation_goal")
    if isinstance(goal, dict):
        speech += f" My active navigation goal is x={float(goal.get('x', 0.0)):.2f}, y={float(goal.get('y', 0.0)):.2f}."
    return speech

def is_current_vision_request(command: str) -> bool:
    t = normalize_user_text(command)
    asks_search_location = bool(
        re.search(r"\b(where\s+(?:is|are)|locate|find|search|look\s+for|go\s+to)\b", t)
    )
    asks_current_view = any(p in t for p in [
        "camera",
        "current view",
        "right now",
        "visible",
        "see",
        "detect",
        "around you",
        "in front of you",
    ])
    if asks_search_location and not asks_current_view:
        return False
    if any(p in t for p in [
        "what do you see",
        "what can you see",
        "what you see",
        "describe camera",
        "describe what you see",
        "camera view",
        "around you",
        "in front of you",
    ]):
        return True
    if not is_question_like(command):
        return False
    physical_terms = {
        "door", "bottle", "chair", "table", "person", "human", "cup", "bag",
        "suitcase", "box", "wall", "room", "object", "thing", "purple",
        "red", "blue", "green", "yellow", "black", "white", "left", "right",
        "front", "behind", "camera", "see", "visible", "detect",
    }
    return (
        bool(re.search(r"\b(what|where|is|are|do|does|can|could|see|detect)\b", t))
        and any(term in t for term in physical_terms)
    )

def describe_current_vision(command: str, vision_desc: str, objects: list) -> str:
    if objects:
        return vision_desc
    t = command.lower()
    target_match = re.search(r"\b(?:the\s+)?([a-z][a-z0-9 _-]{1,30})\b", t)
    if "door" in t:
        return "I do not see a door in the current camera view. I can search the map for the door if you ask me to search for it."
    if target_match and not any(p in t for p in ["what do you see", "what can you see", "camera"]):
        return f"{vision_desc} I do not see that target in the current camera view."
    return vision_desc

def build_brain_context(map_name: str, robot: Robot, places_memory: PlacesMemory,
                        vision_desc: str, lidar_distance, objects: list) -> dict:
    state = robot.get_mobile_state()
    pose = "not localized"
    if state.get("localized"):
        pose = (
            f"x={float(state.get('x', 0.0)):.2f}, "
            f"y={float(state.get('y', 0.0)):.2f}, "
            f"yaw={math.degrees(float(state.get('yaw', 0.0))):.0f}deg, "
            f"age={float(state.get('pose_age', 0.0)):.1f}s"
        )
    goal = state.get("navigation_goal")
    goal_text = ""
    if isinstance(goal, dict):
        goal_text = f"x={float(goal.get('x', 0.0)):.2f}, y={float(goal.get('y', 0.0)):.2f}"
    names = places_memory.list_places(map_name)
    if names:
        saved_places = f"{len(names)}: {', '.join(names[:8])}"
        if len(names) > 8:
            saved_places += ", ..."
    else:
        saved_places = "none"
    lidar_text = f"{float(lidar_distance):.2f}m front" if lidar_distance is not None else "unknown"
    object_names = ", ".join(
        f"{o.get('name', '?')}@{o.get('position', '?')}"
        for o in (objects or [])[:5]
    ) or "none"
    return {
        "map": map_name,
        "pose": pose,
        "navigation_goal": goal_text,
        "saved_places": saved_places,
        "vision": f"{vision_desc} objects={object_names}",
        "lidar": lidar_text,
        "status": "Use this context for robot questions; do not claim sensors are unavailable.",
    }

def get_lidar_distance(perceptions):
    try:
        if hasattr(perceptions, "get_lidar_distance"):
            return perceptions.get_lidar_distance()
    except Exception:
        pass
    return None

def get_objects_from_vision(vision_data):
    if isinstance(vision_data, dict):
        return vision_data.get("objects", [])
    return []

def get_vision_description(vision_data):
    if isinstance(vision_data, dict):
        return vision_data.get("description", "No camera description.")
    return "No camera description."

def has_movement_verb(text):
    verbs = ["move","go","turn","drive","walk","rotate","advance",
             "reverse","stop","halt","freeze","approach","follow"]
    return any(re.search(r"\b" + v + r"\b", text) for v in verbs)

def is_imperative_motion(command):
    text = command.lower().strip()
    if text in ["left", "right", "forward", "backward", "back", "ahead"]:
        return True
    return has_movement_verb(text)

def detect_movement_intent(command, objects):
    text = command.lower().strip()
    objects_lower = [str(o.get("name","")).lower() for o in objects] if objects else []

    if re.search(r"\b(stop|halt|freeze)\b", text) or "don't move" in text:
        return "stop"

    short = text in ["left","right","forward","backward","back","ahead"]
    if not has_movement_verb(text) and not short:
        return None

    if re.search(r"\b(forward|ahead|straight)\b", text):
        return "forward"
    if re.search(r"\b(backward|reverse)\b", text) or text in ["back","move back","go back"]:
        return "backward"
    if re.search(r"\bleft\b", text):
        return "left"
    if re.search(r"\bright\b", text):
        return "right"

    if any(p in text for p in ["move to the person","go to the person","follow the person"]):
        return "approach_person" if "person" in objects_lower else "search_person"

    return None

def detect_arm_intent(command):
    t = command.lower()
    if any(w in t for w in ["drop","release","put down"]):
        return "drop"
    if any(w in t for w in ["pick","grab","take","hold"]):
        return "pickup"
    return None

def normalize_model_data(data):
    if not isinstance(data, dict):
        data = {}
    speech = str(data.get("speech", "")).strip()
    if not speech:
        speech = "I am ready."
    thought = str(data.get("thought", ""))
    action  = data.get("action", {}) if isinstance(data.get("action"), dict) else {}

    move = str(action.get("move", "stop")).lower().strip()
    if move not in {"forward", "backward", "left", "right", "stop"}:
        move = "stop"

    arm = str(action.get("arm", "home")).lower().strip()
    if arm not in {"searching", "pickup", "drop", "home"}:
        arm = "home"

    try:
        speed = float(action.get("speed", 0.0))
    except Exception:
        speed = 0.0
    speed = max(0.0, min(1.0, speed))
    if move == "stop":
        speed = 0.0

    return {"thought": thought, "speech": speech,
            "action": {"move": move, "speed": speed, "arm": arm}}

def build_safe_action(command, model_data, vision_data, lidar_distance):
    data    = normalize_model_data(model_data)
    objects = get_objects_from_vision(vision_data)
    vision_desc = get_vision_description(vision_data)

    move_intent = detect_movement_intent(command, objects)
    arm_intent  = detect_arm_intent(command)

    model_action = data.get("action", {}) if isinstance(data.get("action"), dict) else {}
    move = model_action.get("move", "stop")
    speed = model_action.get("speed", 0.0)
    arm = model_action.get("arm", "home")

    if move != "stop" and not is_imperative_motion(command):
        move, speed = "stop", 0.0
        if not str(data.get("speech", "")).strip():
            data["speech"] = "I am ready."

    if move_intent == "forward":
        move, speed = "forward", DEFAULT_FORWARD_SPEED
    elif move_intent == "backward":
        move, speed = "backward", DEFAULT_BACKWARD_SPEED
    elif move_intent == "left":
        move, speed = "left", DEFAULT_TURN_SPEED
    elif move_intent == "right":
        move, speed = "right", DEFAULT_TURN_SPEED
    elif move_intent == "stop":
        data["speech"] = "Stopping now."
    elif move_intent == "approach_person":
        move, speed = "forward", PERSON_FORWARD_SPEED
        data["speech"] = "Approaching the person slowly."
    elif move_intent == "search_person":
        move, speed = "left", 0.15
        data["speech"] = "I don't see a person yet. Turning to search."

    if any(p in command.lower() for p in
           ["what you see","what do you see","what can you see","describe","camera"]):
        move, speed, arm = "stop", 0.0, "home"
        data["speech"] = vision_desc

    if arm_intent:
        arm = arm_intent
    else:
        arm = "home"

    # Lidar forward guard
    if move == "forward":
        if lidar_distance is None:
            move, speed = "stop", 0.0
            data["speech"] = "Lidar not ready — cannot move forward safely."
        else:
            try:
                d = float(lidar_distance)
                if d < OBSTACLE_STOP_DISTANCE:
                    move, speed = "stop", 0.0
                    data["speech"] = "Obstacle ahead — stopping."
                elif d < OBSTACLE_SLOW_DISTANCE:
                    speed = min(speed, 0.12)
                    data["speech"] = "Moving forward slowly — something nearby."
            except Exception:
                move, speed = "stop", 0.0

    if move != "stop":
        speed = max(0.10, min(speed, MAX_SAFE_SPEED))
    else:
        speed = 0.0

    data["action"] = {"move": move, "speed": speed, "arm": arm}
    return data

# ──────────────────────────────────────────────────────────────────
# Main loop
# ──────────────────────────────────────────────────────────────────

def main():
    face_process = start_robot_face()

    brain       = Brain(model_name="Qwen/Qwen2.5-1.5B-Instruct")
    perceptions = Perceptions()
    robot       = Robot()
    face = FaceBridge()
    map_name = os.environ.get("AI_MAP_NAME", "salle_robotique")
    places_memory = PlacesMemory()
    search_task = MapSearchTask(
        robot, perceptions,
        map_name=map_name,
        status_callback=lambda status: face_show_search_status(face, status),
    )
    command_bus = CommandBus(max_size=32)
    mobile_gateway = None
    operation_lock = threading.Lock()
    operation_generation = 0

    def invalidate_operation():
        nonlocal operation_generation
        with operation_lock:
            operation_generation += 1
            return operation_generation

    def current_operation_generation():
        with operation_lock:
            return operation_generation

    def execute_action_if_current(generation, move_cmd, speed, arm_cmd):
        """Serialize the final generation check with action dispatch."""
        with operation_lock:
            if generation != operation_generation:
                return False
            robot.move(move_cmd, speed)
            robot.control_arm(arm_cmd)
            return True

    mobile_control = MobileTeleopController(
        robot,
        search_task,
        status_callback=lambda status: face.send_status(status),
        preempt_callback=invalidate_operation,
    )

    def stop_all(scope="all", request_id="", source="interface"):
        print(f"[STOP] source={source} scope={scope} request={request_id}", flush=True)
        invalidate_operation()
        search_task.request_cancel()
        mobile_control.stop(reason="stopped")
        robot.stop()
        threading.Thread(target=robot.cancel_navigation, daemon=True).start()
        speech = "Stopping search, navigation, and robot movement now."
        face.send_emotion("neutral")
        face.send_status({
            "mode": "stopped",
            "phase": "stopped",
            "message": speech,
            "can_talk": True,
        })
        face.send_message(speech)
        if mobile_gateway is not None and request_id:
            mobile_gateway.publish_response(request_id, speech, source, status="stopped")
        if source == "robot_mic":
            threading.Thread(target=perceptions.speak, args=("Stopping now.",), daemon=True).start()

    def cancel_search(request_id="", source="interface"):
        print(f"[SEARCH CANCEL] source={source} request={request_id}", flush=True)
        invalidate_operation()
        search_task.request_cancel()
        robot.stop()
        threading.Thread(target=robot.cancel_navigation, daemon=True).start()
        speech = "The current search is being cancelled."
        face.send_status({
            "mode": "stopped",
            "phase": "cancelled",
            "message": speech,
            "can_talk": True,
        })
        if mobile_gateway is not None and request_id:
            mobile_gateway.publish_response(request_id, speech, source, status="cancelled")

    def show_rviz(request_id="", source="interface"):
        def _launch():
            ok = robot.launch_rviz()
            speech = "RViz opened." if ok else "RViz could not be opened on the robot display."
            face.send_status({
                "mode": "ready" if ok else "not_found",
                "phase": "rviz",
                "message": speech,
                "can_talk": True,
            })
            if mobile_gateway is not None and request_id:
                mobile_gateway.publish_response(
                    request_id, speech, source,
                    status="completed" if ok else "failed",
                )
        threading.Thread(target=_launch, daemon=True).start()

    def set_robot_microphone(enabled: bool, request_id="", source="interface"):
        enabled = bool(enabled)
        perceptions.set_mute(not enabled)
        print(f"[ROBOT MIC] source={source} enabled={enabled}", flush=True)
        if mobile_gateway is not None:
            mobile_gateway.publish_robot_mic_state(enabled, source=source)

    mobile_gateway = MobileGateway(
        command_bus=command_bus,
        map_provider=lambda: robot.get_map_snapshot(map_name, force=True),
        telemetry_provider=robot.get_mobile_state,
        stop_callback=stop_all,
        search_cancel_callback=cancel_search,
        teleop_callback=mobile_control.update,
        rviz_callback=show_rviz,
        mic_control_callback=set_robot_microphone,
        mic_state_provider=lambda: not perceptions.is_muted,
    )
    face.add_listener(mobile_gateway.broadcast_event)
    mobile_gateway.start()

    microphone_commands = MicrophoneCommandProducer(
        perceptions,
        command_bus,
        emergency_callback=stop_all,
        search_cancel_callback=cancel_search,
    )
    microphone_commands.start()

    ui_commands = UiCommandListener(
        robot, search_task, face, perceptions,
        stop_search_callback=cancel_search,
        mic_control_callback=set_robot_microphone,
    )
    ui_commands.start()

    print("--- 1.5B SYSTEM ACTIVE ---", flush=True)
    face.send_emotion("happy")
    face.send_status({
        "mode": "ready",
        "phase": "listening",
        "message": "Ready. You can speak now.",
        "can_talk": True,
    })
    face.send_message("AI companion ready.")

    try:
        while True:
            envelope = command_bus.get(timeout=0.25)
            if envelope is None:
                continue

            # Capture current sensor context immediately before processing.
            frame, vision_data = perceptions.see()
            lidar_distance     = get_lidar_distance(perceptions)
            objects            = get_objects_from_vision(vision_data)
            vision_desc        = get_vision_description(vision_data)
            brain_context      = build_brain_context(
                map_name, robot, places_memory, vision_desc,
                lidar_distance, objects,
            )

            command = envelope.text
            command_generation = current_operation_generation()
            mobile_gateway.publish_processing(envelope.request_id, command, envelope.source)

            print(f"\nUser ({envelope.source}, {envelope.request_id}): {command}", flush=True)

            # ── PLACE MEMORY COMMANDS ───────────────────────────
            # 1. Save place
            save_target = parse_save_place(command)
            if save_target:
                pose = robot.get_amcl_pose()
                if pose is None:
                    speech = "I cannot save the location because my localization is not ready."
                    print(f"[PLACES MEMORY] Save failed: pose not ready", flush=True)
                else:
                    places_memory.save_place(map_name, save_target, pose["x"], pose["y"], pose["yaw"])
                    speech = f"Location saved as {save_target}."
                    print(f"[PLACES MEMORY] Saved place '{save_target}' at x={pose['x']:.2f}, y={pose['y']:.2f}, yaw={pose['yaw']:.2f}", flush=True)

                deliver_response(perceptions, face, mobile_gateway, envelope, command, speech)
                continue

            # 2. Rename place
            rename_res = parse_rename_place(command)
            if rename_res:
                old_n, new_n = rename_res
                if places_memory.rename_place(map_name, old_n, new_n):
                    speech = f"I have renamed {old_n} to {new_n} in my memory."
                else:
                    speech = f"I could not find a place named {old_n} in my memory."
                deliver_response(perceptions, face, mobile_gateway, envelope, command, speech)
                continue

            # 3. Delete place
            delete_target = parse_delete_place(command)
            if delete_target:
                if places_memory.remove_place(map_name, delete_target):
                    speech = f"I have removed {delete_target} from my memory."
                else:
                    speech = f"I could not find a place named {delete_target} in my memory."
                deliver_response(perceptions, face, mobile_gateway, envelope, command, speech)
                continue

            # 4. List places
            if is_list_places_request(command):
                names = places_memory.list_places(map_name)
                if names:
                    speech = f"I know the following places on this map: {', '.join(names)}."
                else:
                    speech = "I do not have any saved places for this map yet."
                deliver_response(perceptions, face, mobile_gateway, envelope, command, speech)
                continue

            # 5. Count places in memory
            if is_places_count_request(command):
                names = places_memory.list_places(map_name)
                count = len(names)
                if count == 0:
                    speech = "I do not have any saved places for this map yet."
                elif count == 1:
                    speech = f"I have 1 saved place on this map: {names[0]}."
                else:
                    speech = f"I have {count} saved places on this map: {', '.join(names)}."
                deliver_response(perceptions, face, mobile_gateway, envelope, command, speech)
                continue

            # 6. Exact map identity questions must not depend on the LLM.
            if is_map_name_request(command):
                speech = describe_map_identity(map_name, places_memory)
                deliver_response(perceptions, face, mobile_gateway, envelope, command, speech)
                continue

            # 7. Current named-place questions compare AMCL pose to saved places.
            if is_current_place_request(command):
                speech = describe_current_place(robot, map_name, places_memory)
                deliver_response(perceptions, face, mobile_gateway, envelope, command, speech)
                continue

            # 8. Robot position/status questions must not become a search for "you".
            if is_robot_pose_request(command):
                speech = describe_robot_pose(robot, map_name)
                deliver_response(perceptions, face, mobile_gateway, envelope, command, speech)
                continue

            # 9. Camera/vision questions should answer from live sensor data, not
            # from the general language model.
            if is_current_vision_request(command):
                speech = describe_current_vision(command, vision_desc, objects)
                deliver_response(perceptions, face, mobile_gateway, envelope, command, speech)
                continue

            # ── SEARCH REQUEST ─────────────────────────────────
            if is_search_request(command):
                target = parse_search_target(command)

                # Check if target is a saved place
                saved_place = places_memory.get_place(map_name, target)
                if saved_place:
                    print(f"[TASK] NAVIGATING TO PLACE: '{target}' at x={saved_place['x']:.2f}, y={saved_place['y']:.2f}", flush=True)
                    speech_start = f"I am going to {target}."
                    perceptions.speak(speech_start)
                    face.send_message(f"You: {command}\nAI: {speech_start}")

                    face_show_search(
                        face, target, "starting", 0, 0, objects,
                        message=speech_start, can_talk=False
                    )

                    if command_generation != current_operation_generation():
                        mobile_gateway.publish_response(
                            envelope.request_id,
                            "Navigation command cancelled.",
                            envelope.source,
                            status="cancelled",
                        )
                        continue

                    robot.cancel_navigation()
                    robot.stop()
                    if hasattr(robot, "clear_costmaps"):
                        robot.clear_costmaps()

                    nav_goals = build_place_navigation_goals(robot, map_name, saved_place)
                    route_total = len(nav_goals)
                    nav_timeout = float(os.environ.get("AI_PLACE_NAV_TIMEOUT", "35.0"))
                    arrival_radius = float(os.environ.get("AI_PLACE_ARRIVAL_RADIUS", "0.55"))
                    approach_radius = float(os.environ.get("AI_PLACE_APPROACH_RADIUS", "1.10"))
                    goal_radius = float(os.environ.get("AI_PLACE_GOAL_RADIUS", "0.55"))
                    start_grace = float(os.environ.get("AI_PLACE_START_GRACE_SECONDS", "5.0"))
                    arrived = False
                    interrupted = False
                    reached_approach = False
                    stuck_timeout = float(os.environ.get("AI_PLACE_STUCK_TIMEOUT", "8.0"))
                    attempted_routes = 0
                    started_routes = 0
                    last_route_error = "no route was tried"
                    progress_thresh = 0.10

                    try:
                        if hasattr(perceptions, "set_moving"):
                            perceptions.set_moving(True)

                        for route_idx, goal in enumerate(nav_goals, start=1):
                            attempted_routes = route_idx
                            if command_generation != current_operation_generation():
                                interrupted = True
                                break

                            if route_idx > 1:
                                robot.cancel_navigation()
                                robot.stop()
                                if hasattr(robot, "clear_costmaps"):
                                    robot.clear_costmaps()

                            route_kind = "saved point" if goal.get("kind") == "saved" else "safe approach"
                            print(
                                f"[TASK] PLACE ROUTE {route_idx}/{route_total}: "
                                f"{route_kind} x={goal['x']:.2f}, y={goal['y']:.2f}, yaw={goal.get('yaw', 0.0):.2f}",
                                flush=True,
                            )
                            face.send_status({
                                "mode": "searching",
                                "target": target,
                                "phase": "navigating",
                                "waypoint_index": route_idx,
                                "waypoint_total": route_total,
                                "searched_count": max(0, route_idx - 1),
                                "found": False,
                                "message": f"Trying route {route_idx}/{route_total} to {target}",
                                "objects": objects[:4],
                                "can_talk": False,
                            })

                            goal_started = robot.goto_map(goal["x"], goal["y"], goal.get("yaw", 0.0))
                            if not goal_started:
                                last_route_error = f"route {route_idx} could not start"
                                if command_generation != current_operation_generation():
                                    interrupted = True
                                    break
                                print(f"[NAV ROUTE] {last_route_error}; trying next approach", flush=True)
                                continue
                            started_routes += 1

                            t_start = time.time()
                            last_progress = time.time()
                            best_route_d = None
                            route_reached = False

                            while time.time() - t_start < nav_timeout:
                                if command_generation != current_operation_generation():
                                    interrupted = True
                                    break

                                d_target = robot.distance_to(saved_place["x"], saved_place["y"])
                                d_goal = robot.distance_to(goal["x"], goal["y"])
                                distance_msg = f"Navigating to {target} via route {route_idx}/{route_total}"
                                if d_target is not None:
                                    distance_msg = f"Distance to {target}: {d_target:.2f} m"
                                    if d_target <= arrival_radius:
                                        arrived = True
                                        break

                                if d_goal is not None:
                                    progress_d = d_goal
                                    if d_goal <= goal_radius:
                                        if goal.get("kind") != "saved" and (d_target is None or d_target <= approach_radius):
                                            arrived = True
                                            reached_approach = True
                                            break
                                        if goal.get("kind") == "saved":
                                            arrived = True
                                            break
                                        route_reached = True
                                        last_route_error = f"route {route_idx} reached but was not close enough to {target}"
                                        break
                                else:
                                    progress_d = d_target

                                if progress_d is not None:
                                    if best_route_d is None or progress_d < best_route_d - progress_thresh:
                                        best_route_d = progress_d
                                        last_progress = time.time()
                                    elif (
                                        time.time() - t_start >= start_grace
                                        and time.time() - last_progress > stuck_timeout
                                    ):
                                        last_route_error = f"route {route_idx} made no progress"
                                        print(f"[NAV ROUTE] {last_route_error}; trying next approach", flush=True)
                                        break
                                else:
                                    last_progress = time.time()

                                face.send_status({
                                    "mode": "searching",
                                    "target": target,
                                    "phase": "navigating",
                                    "waypoint_index": route_idx,
                                    "waypoint_total": route_total,
                                    "searched_count": max(0, route_idx - 1),
                                    "found": False,
                                    "message": distance_msg,
                                    "objects": objects[:4],
                                    "can_talk": False,
                                })
                                time.sleep(0.25)
                            else:
                                last_route_error = f"route {route_idx} timed out"

                            if interrupted or arrived:
                                break

                            robot.cancel_navigation()
                            robot.stop()
                            robot.clear_navigation_goal()
                            if route_reached:
                                print(f"[NAV ROUTE] {last_route_error}; trying next approach", flush=True)
                    finally:
                        if hasattr(perceptions, "set_moving"):
                            perceptions.set_moving(False)
                        robot.stop()
                        robot.clear_navigation_goal()

                    if interrupted:
                        robot.cancel_navigation()

                    if interrupted:
                        speech = f"Navigation to {target} was cancelled."
                        phase = "cancelled"
                        found = False
                    elif arrived:
                        if reached_approach:
                            speech = f"I reached a safe approach near {target}."
                        else:
                            speech = f"I have successfully arrived at {target}."
                        phase = "found"
                        found = True
                    else:
                        if started_routes == 0:
                            speech = f"I could not start any route to {target}."
                        else:
                            speech = (
                                f"I could not find a reachable path to {target} "
                                f"after trying {attempted_routes} route{'s' if attempted_routes != 1 else ''}."
                            )
                        phase = "not_found"
                        found = False

                    print(f"[TASK RESULT]: arrived={arrived} speech={speech} detail={last_route_error}", flush=True)
                    deliver_response(
                        perceptions, face, mobile_gateway, envelope, command, speech,
                        status="cancelled" if interrupted else ("completed" if arrived else "failed"),
                        arrived=arrived,
                    )
                    face_show_search(
                        face, target, phase, max(1, attempted_routes), max(1, route_total), objects,
                        found=found, searched_count=attempted_routes, message=speech, can_talk=True
                    )
                    continue

                # Validate before accepting. Unknown visual targets are handled
                # by the optional open-vocabulary detector in perceptions.py.
                yolo_name = resolve_yolo_name(target)
                if not is_searchable_target(target):
                    speech = (f"I cannot search for '{target}'. "
                              f"Please name a real visual target, like a person, door, key, cube, chair, or bottle.")
                    print(f"[SEARCH REJECTED]: {target}", flush=True)
                    face.send_emotion("sad")
                    face.send_status({
                        "mode": "not_found",
                        "target": target,
                        "phase": "rejected",
                        "message": speech,
                        "can_talk": True,
                    })
                    deliver_response(
                        perceptions, face, mobile_gateway, envelope, command, speech,
                        status="rejected",
                    )
                    continue

                detector = yolo_name or "open-vocabulary"
                print(f"[TASK] MAP SEARCH: {detector} (from '{target}')", flush=True)
                speech_start = f"I will search the map for {target}."
                perceptions.speak(speech_start)
                face.send_message(f"You: {command}\nAI: {speech_start}")
                face_show_search(
                    face, target, "starting", 0, 0, objects,
                    message=speech_start, can_talk=False
                )

                if command_generation != current_operation_generation():
                    mobile_gateway.publish_response(
                        envelope.request_id,
                        "Search command cancelled.",
                        envelope.source,
                        status="cancelled",
                    )
                    continue

                try:
                    result = search_task.search(target)
                finally:
                    if hasattr(perceptions, "set_search_target"):
                        perceptions.set_search_target(None)
                speech  = result.get("speech", f"Search for {target} complete.")
                found   = result.get("found", False)
                where   = result.get("where", "")
                phase   = "cancelled" if result.get("cancelled") else ("found" if found else "not_found")
                searched_count = len(getattr(search_task, "_visited", []))
                waypoint_total = int(getattr(search_task, "_total_waypoints", 0) or 0)
                if waypoint_total > 0:
                    searched_count = min(searched_count, waypoint_total)

                print(f"[TASK RESULT]: found={found} speech={speech}", flush=True)
                deliver_response(
                    perceptions, face, mobile_gateway, envelope, command, speech,
                    status="cancelled" if result.get("cancelled") else "completed",
                    found=bool(found),
                    where=where,
                )
                face_show_search(
                    face, target, phase,
                    searched_count if searched_count else 0,
                    waypoint_total, objects, found=found,
                    searched_count=searched_count,
                    message=f"{speech} {where}".strip(),
                    can_talk=True,
                )
                continue

            # ── CONVERSATIONAL / MOTION COMMAND ───────────────
            face_show_thinking(face, command)

            raw_response, latency = brain.process_command(
                command,
                vision_data=vision_data,
                lidar_distance=lidar_distance,
                robot_context=brain_context,
            )

            # raw_response is always a dict from the upgraded brain
            if isinstance(raw_response, dict):
                model_data = raw_response
            else:
                model_data = {"speech": str(raw_response), "thought": "", "action": {}}

            safe_data = build_safe_action(
                command=command,
                model_data=model_data,
                vision_data=vision_data,
                lidar_distance=lidar_distance,
            )

            if command_generation != current_operation_generation():
                robot.stop()
                mobile_gateway.publish_response(
                    envelope.request_id,
                    "Command cancelled by a newer safety command.",
                    envelope.source,
                    status="cancelled",
                )
                continue

            speech    = safe_data.get("speech", "")
            thought   = safe_data.get("thought", "")
            action    = safe_data.get("action", {})
            move_cmd  = action.get("move", "stop")
            speed     = action.get("speed", 0.0)
            arm_cmd   = action.get("arm", "home")

            # Clean print — not a dict repr
            print(f"[AI SPEECH]: {speech}", flush=True)
            print(f"[AI THOUGHT]: {thought}", flush=True)
            print(f"[ACTION]: move={move_cmd} speed={speed} arm={arm_cmd}", flush=True)
            print(f"[VISION]: {vision_desc}", flush=True)
            print(f"[LIDAR]: {lidar_distance}", flush=True)
            print(f"[LATENCY]: {latency:.2f}s", flush=True)

            # Face update with all context
            face_show_response(
                face, command, speech, thought,
                vision_desc, objects, lidar_distance, latency
            )

            # Speak the speech string only — NOT the dict
            perceptions.speak(speech)
            if not execute_action_if_current(
                command_generation, move_cmd, speed, arm_cmd
            ):
                robot.stop()
                mobile_gateway.publish_response(
                    envelope.request_id,
                    "Command cancelled before movement.",
                    envelope.source,
                    status="cancelled",
                )
                continue
            mobile_gateway.publish_response(
                envelope.request_id,
                speech,
                envelope.source,
                status="completed",
                latency_ms=round(float(latency) * 1000.0, 1),
            )

    except KeyboardInterrupt:
        print("\n[MAIN] Stopping...", flush=True)
    finally:
        try:
            microphone_commands.stop()
            mobile_control.close()
            mobile_gateway.stop()
        except Exception:
            pass
        try:
            face.send_emotion("neutral")
            face.send_status({
                "mode": "stopped",
                "phase": "stopped",
                "message": "System stopped.",
                "can_talk": False,
            })
            face.send_message("System stopped.")
            robot.move("stop", 0.0)
        except Exception:
            pass
        stop_robot_face(face_process)

if __name__ == "__main__":
    main()
