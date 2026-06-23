"""
search_tasks.py — Professional map search for Rosmaster X3 PLUS
================================================================
Key fixes vs original:
 1. No Docker call inside the 0.25 s poll — uses robot.distance_to()
    which now reads the background-cached AMCL pose (instant)
 2. Only search for YOLO-known objects; if the word is unknown, the robot
    says so immediately instead of wandering forever
 3. Visit radius 0.60 m — avoids re-visiting the same spot
 4. Scan uses ROS yaw (from cached pose) to orient turns instead of
    always spinning right × 3
 5. Recovery rotate uses 45° left burst, then resumes
 6. approach_if_visible moves with lidar guard
"""

import time
import math
import re
import os
import threading
from typing import Callable, Dict, List, Optional, Tuple

# ── Known YOLO classes (COCO 80) ─────────────────────────────────
YOLO_CLASSES = {
    "person","bicycle","car","motorcycle","airplane","bus","train","truck",
    "boat","traffic light","fire hydrant","stop sign","parking meter","bench",
    "bird","cat","dog","horse","sheep","cow","elephant","bear","zebra","giraffe",
    "backpack","umbrella","handbag","tie","suitcase","frisbee","skis","snowboard",
    "sports ball","kite","baseball bat","baseball glove","skateboard","surfboard",
    "tennis racket","bottle","wine glass","cup","fork","knife","spoon","bowl",
    "banana","apple","sandwich","orange","broccoli","carrot","hot dog","pizza",
    "donut","cake","chair","couch","potted plant","bed","dining table","toilet",
    "tv","laptop","mouse","remote","keyboard","cell phone","microwave","oven",
    "toaster","sink","refrigerator","book","clock","vase","scissors",
    "teddy bear","hair drier","toothbrush",
}

YOLO_ALIASES = {
    "tv":         ["tv","television","screen","monitor","display"],
    "cell phone": ["phone","mobile","cellphone","smartphone"],
    "couch":      ["sofa","couch","settee"],
    "potted plant":["plant","flower","cactus"],
    "dining table":["table","desk"],
    "laptop":     ["laptop","computer","notebook"],
    "backpack":   ["backpack","bag","rucksack"],
    "person":     ["person","human","man","woman","boy","girl","student","teacher","someone"],
    "chair":      ["chair","seat","stool"],
    "book":       ["book","textbook","notebook"],
    "bottle":     ["bottle","flask"],
    "cup":        ["cup","mug","glass"],
    "keyboard":   ["keyboard"],
    "mouse":      ["mouse"],
    "clock":      ["clock","watch"],
    "vase":       ["vase","pot"],
}

def resolve_yolo_name(target_text: str) -> Optional[str]:
    """Map user text to the nearest YOLO class name, or None."""
    t = target_text.lower().strip()
    # Direct match
    if t in YOLO_CLASSES:
        return t
    # Alias match
    for canon, aliases in YOLO_ALIASES.items():
        if t == canon or any(a in t or t in a for a in aliases):
            return canon
    # Substring match in class set
    for cls in YOLO_CLASSES:
        if cls in t or t in cls:
            return cls
    return None

# ── Noise words that indicate a mis-hear, not a real object ──────
_NON_SEARCHABLE = {
    "map","floor","wall","room","area","place","space","the","a","an",
    "here","there","somewhere","thing","stuff","object","it","you","u",
    "me","myself","yourself","robot",
}

def is_searchable_target(text: str) -> bool:
    t = text.lower().strip()
    if t in _NON_SEARCHABLE:
        return False
    if len(t) < 2:
        return False
    return True

def normalize_command_text(text: str) -> str:
    t = str(text).lower().strip()
    t = re.sub(r"[?.!,]", " ", t)
    t = re.sub(r"\s+", " ", t).strip()
    for bad, good in [
        ("for in the ","find the "),("for in a ","find a "),("for in ","find "),
        ("end the ","find the "),("end a ","find a "),("fine the ","find the "),
        ("fine a ","find a "),("found the ","find the "),("phone the ","find the "),
        ("fund the ","find the "),
    ]:
        if t.startswith(bad):
            t = good + t[len(bad):]
            break
    return re.sub(r"\s+", " ", t).strip()

def is_search_request(text: str) -> bool:
    t = normalize_command_text(text)
    if re.search(r"\bwhere\s+(?:are|r)\s+(?:you|u|the robot)\b", t):
        return False
    if re.search(r"\bwhat\s+is\s+(?:your|the robot(?:'s)?)\s+(?:position|location|pose)\b", t):
        return False
    return any(p in t for p in [
        "search for","look for","find","where is","where are",
        "locate","go find","go to","can you find","please find",
    ])

def parse_search_target(text: str) -> str:
    t = normalize_command_text(text)
    # Remove common speech filler before extracting the object name.
    t = re.sub(r"\b(okay|ok|yeah|yes|alright|robot|please)\b", " ", t)
    for rm in ["can you","could you","search for","look for",
               "go find","find","where is","where are","locate","go to",
               "the ","a ","an "]:
        t = t.replace(rm, " ")
    tokens = [tok for tok in re.sub(r"\s+", " ", t).strip().split(" ") if tok]
    while tokens and (len(tokens[0]) == 1 or tokens[0] in {"the", "a", "an", "this", "that", "please", "robot"}):
        tokens.pop(0)
    return " ".join(tokens).strip() or "object"

# ── Main search task ─────────────────────────────────────────────

class MapSearchTask:
    VISIT_RADIUS   = 0.60   # m — don't revisit same spot
    NAV_TIMEOUT    = float(os.environ.get("AI_SEARCH_NAV_TIMEOUT", "14.0"))
    STUCK_TIMEOUT  = float(os.environ.get("AI_SEARCH_STUCK_TIMEOUT", "8.0"))
    START_GRACE_SECONDS = float(os.environ.get("AI_SEARCH_START_GRACE_SECONDS", "5.0"))
    MAX_STUCK_GOALS = int(os.environ.get("AI_SEARCH_MAX_STUCK_GOALS", "4"))
    PROGRESS_THRESH= 0.10   # m improvement to reset stuck timer
    SCAN_DWELL     = 0.7    # s dwell per scan direction
    CONFIRM_READS  = int(os.environ.get("AI_SEARCH_CONFIRM_READS", "1"))
    CONFIRM_INTERVAL = float(os.environ.get("AI_SEARCH_CONFIRM_INTERVAL", "0.08"))
    STATUS_INTERVAL = 1.5    # s between face/status refreshes
    MAX_RECOVERIES_PER_WP = int(os.environ.get("AI_SEARCH_MAX_RECOVERIES_PER_WP", "0"))
    SCAN_IF_WITHIN = 0.90    # scan near misses, skip far stuck points
    WAYPOINT_CHECK_SECONDS = float(os.environ.get("AI_SEARCH_WAYPOINT_CHECK_SECONDS", "0.0"))
    SCAN_TURN_SECONDS = float(os.environ.get("AI_SEARCH_SCAN_SECONDS", "0.0"))
    START_SCAN_SECONDS = float(os.environ.get("AI_SEARCH_START_SCAN_SECONDS", "0.0"))
    FULL_SCAN_EVERY = int(os.environ.get("AI_SEARCH_FULL_SCAN_EVERY", "0"))
    STOP_AT_WAYPOINT = os.environ.get("AI_SEARCH_STOP_AT_WAYPOINT", "0") == "1"
    APPROACH_ON_FOUND = os.environ.get("AI_SEARCH_APPROACH_ON_FOUND", "0") == "1"
    ROTATE_ON_STUCK = os.environ.get("AI_SEARCH_ROTATE_ON_STUCK", "0") == "1"

    def __init__(self, robot, perceptions, map_name="salle_robotique",
                 status_callback: Optional[Callable[[Dict], None]] = None):
        self.robot       = robot
        self.perceptions = perceptions
        self.map_name    = map_name
        self._status_callback = status_callback
        self._visited: List[Dict] = []
        self._last_vision: Dict = {"objects": [], "description": ""}
        self._last_status_emit = 0.0
        self._current_target = ""
        self._total_waypoints = 0
        self._all_waypoints: List[Dict] = []
        self._current_waypoint: Optional[Dict] = None
        self._cancel_event = threading.Event()

    def _dist(self, a, b):
        return math.hypot(float(a["x"])-float(b["x"]), float(a["y"])-float(b["y"]))

    def _already_visited(self, pt):
        return any(self._dist(pt, v) < self.VISIT_RADIUS for v in self._visited)

    def _mark_visited(self, pt, scanned=True, note="searched"):
        if self._already_visited(pt):
            return
        self._visited.append({
            "x": float(pt["x"]),
            "y": float(pt["y"]),
            "target": self._current_target,
            "scanned": bool(scanned),
            "note": note,
            "time": time.time(),
        })

    def set_status_callback(self, callback: Optional[Callable[[Dict], None]]):
        self._status_callback = callback

    def request_cancel(self):
        self._cancel_event.set()

    def clear_cancel(self):
        self._cancel_event.clear()

    def is_cancel_requested(self) -> bool:
        return self._cancel_event.is_set()

    def _reset_search_memory(self, target_text: str):
        self._visited = []
        self._last_status_emit = 0.0
        self._current_target = target_text
        self._total_waypoints = 0
        self._all_waypoints = []
        self._current_waypoint = None
        self.clear_cancel()

    def _emit_status(self, target_text: str, phase: str,
                     waypoint_index: int = 0, waypoint_total: int = 0,
                     found: bool = False, message: str = "",
                     force: bool = False, mode: Optional[str] = None,
                     can_talk: Optional[bool] = None):
        if self._status_callback is None:
            return
        now = time.time()
        if not force and now - self._last_status_emit < self.STATUS_INTERVAL:
            return
        self._last_status_emit = now

        objects = []
        if isinstance(self._last_vision, dict):
            objects = self._last_vision.get("objects", []) or []

        display_total = int(waypoint_total or self._total_waypoints or 0)
        display_searched = len(self._visited)
        if display_total > 0:
            display_searched = min(display_searched, display_total)

        payload = {
            "mode": mode or ("found" if found else "searching"),
            "target": target_text,
            "phase": phase,
            "waypoint_index": int(waypoint_index or 0),
            "waypoint_total": display_total,
            "searched_count": display_searched,
            "found": bool(found),
            "message": message,
            "objects": objects[:4],
            "can_talk": bool(can_talk) if can_talk is not None else False,
            "current_waypoint": dict(self._current_waypoint) if self._current_waypoint else None,
        }
        try:
            self._status_callback(payload)
        except Exception as e:
            print(f"[SEARCH STATUS ERROR] {e}", flush=True)

    def _cancelled_result(self, target_text: str) -> Dict:
        msg = f"Search for {target_text} stopped from the interface."
        try:
            self.robot.cancel_navigation()
            self.robot.stop()
        except Exception:
            pass
        self._emit_status(
            target_text, "cancelled", found=False, message=msg,
            force=True, mode="stopped", can_talk=True
        )
        return {"found": False, "speech": msg, "where": "cancelled", "cancelled": True}

    def _sleep_interruptible(self, seconds: float) -> bool:
        end = time.time() + max(0.0, float(seconds))
        while time.time() < end:
            if self.is_cancel_requested():
                try:
                    self.robot.stop()
                except Exception:
                    pass
                return False
            time.sleep(min(0.10, max(0.0, end - time.time())))
        return True

    # ── Vision helpers ───────────────────────────────────────────

    def _read_vision(self, force: bool = False) -> Dict:
        vision = {"objects": [], "description": ""}
        try:
            try:
                result = self.perceptions.see(force=force)
            except TypeError:
                result = self.perceptions.see()
            if isinstance(result, tuple) and len(result) >= 2:
                vision = result[1] if isinstance(result[1], dict) else vision
            elif isinstance(result, dict):
                vision = result
        except Exception as e:
            print(f"[SEARCH VISION ERROR] {e}", flush=True)
        self._last_vision = vision
        return vision

    # ── Target detection ─────────────────────────────────────────

    def _normalize_label(self, text: str) -> str:
        return re.sub(r"\s+", " ", str(text).lower().strip())

    def _object_matches(self, obj: dict, target_name: str) -> bool:
        name = self._normalize_label(obj.get("name", ""))
        target = self._normalize_label(target_name)
        yolo_target = resolve_yolo_name(target) or target
        try:
            conf = float(obj.get("confidence", 0.0))
        except Exception:
            conf = 0.0

        if obj.get("source") == "open_vocab":
            return name == target and conf >= 0.12

        min_conf = 0.15 if yolo_target == "person" else 0.25
        return name == yolo_target and conf >= min_conf

    def target_visible(self, yolo_name: str, force: bool = False) -> Tuple[bool, str]:
        vision = self._read_vision(force=force)
        for o in vision.get("objects", []):
            if self._object_matches(o, yolo_name):
                pos  = o.get("position","?")
                dist = o.get("distance_hint","?")
                conf = o.get("confidence", 0)
                return True, f"{yolo_name} pos={pos} dist={dist} conf={conf:.2f}"
        return False, ""

    def target_visible_confirmed(self, yolo_name: str, required_hits: Optional[int] = None,
                                 max_reads: Optional[int] = None,
                                 force: bool = True) -> Tuple[bool, str]:
        required = max(1, int(required_hits or self.CONFIRM_READS))
        reads = max(required, int(max_reads or (required + 1)))
        hits, last_desc = 0, ""
        for i in range(reads):
            found, desc = self.target_visible(yolo_name, force=force)
            if found:
                hits += 1
                last_desc = desc
                if hits >= required:
                    return True, last_desc
            else:
                hits = 0
            if i < reads - 1:
                time.sleep(self.CONFIRM_INTERVAL)
        return False, ""

    def _finish_found(self, yolo_name: str, target_text: str, desc: str,
                      where: str, speech: str,
                      waypoint_index: int = 0, waypoint_total: int = 0) -> Dict:
        try:
            self.robot.cancel_navigation()
            self.robot.stop()
        except Exception:
            pass

        self._emit_status(
            target_text, "found", waypoint_index, waypoint_total,
            found=True, message=desc, force=True, can_talk=True
        )

        if self.APPROACH_ON_FOUND:
            self.approach_if_visible(yolo_name, target_text)
        else:
            try:
                self.robot.stop()
            except Exception:
                pass

        return {"found": True, "speech": speech, "where": where}

    # ── Waypoint selection ───────────────────────────────────────

    def choose_waypoints(self, points: List[Dict], max_points=15) -> List[Dict]:
        pose = self.robot.get_amcl_pose()
        if not points:
            return []

        base = pose if pose else {"x": points[0]["x"], "y": points[0]["y"]}

        candidates = []
        for p in points:
            if self._already_visited(p):
                continue
            d = math.hypot(float(p["x"])-float(base["x"]),
                           float(p["y"])-float(base["y"]))
            if d < 0.4:
                self._mark_visited(p, scanned=True, note="current-area")
                continue
            p2 = dict(p)
            p2["_dist"] = d
            candidates.append(p2)

        # Nearest unsearched areas first. The visited memory prevents loops.
        candidates.sort(key=lambda z: z["_dist"])
        return candidates[:max_points]

    # ── Recovery ─────────────────────────────────────────────────

    def _recovery_rotate(self):
        if not self.ROTATE_ON_STUCK:
            print("[SEARCH RECOVERY] stuck — skipping waypoint without manual rotation", flush=True)
            return
        print("[SEARCH RECOVERY] stuck — rotating 45° left", flush=True)
        self.robot.cancel_navigation()
        self.robot.move("left", speed=0.20, duration=1.5)
        self._sleep_interruptible(2.0)
        self.robot.stop()

    # ── Continuous scan ──────────────────────────────────────────

    def scan_at_waypoint(self, yolo_name: str, target_text: str = "",
                         waypoint_index: int = 0, waypoint_total: int = 0,
                         turn_duration: Optional[float] = None) -> Tuple[bool, str]:
        label_target = target_text or yolo_name
        turn_speed = 0.22
        turn_duration = float(self.SCAN_TURN_SECONDS if turn_duration is None else turn_duration)
        if turn_duration <= 0.0:
            return False, "scan disabled"
        print(f"[SEARCH] turn scan for {yolo_name} ({turn_duration:.2f}s)", flush=True)

        if hasattr(self.perceptions, "set_moving"):
            self.perceptions.set_moving(True)

        start_time = time.time()
        end_time = start_time + turn_duration
        self.robot.move("right", speed=turn_speed, duration=turn_duration)

        try:
            while time.time() < end_time:
                if self.is_cancel_requested():
                    self.robot.stop()
                    return False, "cancelled"

                self._emit_status(
                    label_target, "scanning", waypoint_index, waypoint_total,
                    message="Scanning area", force=True
                )

                found, desc = self.target_visible(yolo_name, force=False)
                if found:
                    print(f"[SEARCH] first visual hit during scan: {desc}", flush=True)
                    self.robot.stop()
                    if hasattr(self.perceptions, "set_moving"):
                        self.perceptions.set_moving(False)

                    found_confirm, desc_confirm = self.target_visible_confirmed(
                        yolo_name, required_hits=1, max_reads=2, force=True
                    )
                    if found_confirm:
                        print(f"[SEARCH] FOUND during scan: {desc_confirm}", flush=True)
                        self.robot.stop()
                        self._emit_status(
                            label_target, "found", waypoint_index, waypoint_total,
                            found=True, message=desc_confirm, force=True
                        )
                        return True, desc_confirm

                    if self.is_cancel_requested():
                        self.robot.stop()
                        return False, "cancelled"

                    remaining = max(0.0, end_time - time.time())
                    if remaining <= 0.25:
                        break
                    if hasattr(self.perceptions, "set_moving"):
                        self.perceptions.set_moving(True)
                    self.robot.move("right", speed=turn_speed, duration=remaining)

                time.sleep(0.15)
        finally:
            if hasattr(self.perceptions, "set_moving"):
                self.perceptions.set_moving(False)
            self.robot.stop()

        return False, "not found in scan"

    def quick_check_at_waypoint(self, yolo_name: str, target_text: str = "",
                                waypoint_index: int = 0, waypoint_total: int = 0,
                                seconds: Optional[float] = None) -> Tuple[bool, str]:
        label_target = target_text or yolo_name
        seconds = float(self.WAYPOINT_CHECK_SECONDS if seconds is None else seconds)
        if seconds <= 0.0:
            return False, "quick check disabled"
        print(f"[SEARCH] quick camera check for {yolo_name} ({seconds:.2f}s)", flush=True)
        try:
            self.robot.stop()
        except Exception:
            pass

        end = time.time() + max(0.0, seconds)
        while time.time() < end:
            if self.is_cancel_requested():
                self.robot.stop()
                return False, "cancelled"
            self._emit_status(
                label_target, "checking_camera", waypoint_index, waypoint_total,
                message="Checking camera", force=True
            )
            found, desc = self.target_visible(yolo_name, force=True)
            if found:
                self.robot.stop()
                self._emit_status(
                    label_target, "found", waypoint_index, waypoint_total,
                    found=True, message=desc, force=True
                )
                return True, desc
            time.sleep(0.08)
        return False, "not found in quick check"

    # ── Approach ─────────────────────────────────────────────────

    def approach_if_visible(self, yolo_name: str, target_text: str = "", max_seconds=8.0):
        print(f"[SEARCH] approaching {yolo_name}", flush=True)
        label_target = target_text or yolo_name
        self._emit_status(label_target, "approaching", found=True,
                          message="Target visible. Approaching carefully.", force=True)
        try:
            if hasattr(self.perceptions, "set_moving"):
                self.perceptions.set_moving(True)
            start = time.time()
            while time.time() - start < max_seconds:
                if self.is_cancel_requested():
                    break
                # Lidar guard
                lidar = None
                try:
                    lidar = self.perceptions.get_lidar_distance()
                except Exception:
                    pass
                if lidar is not None and lidar < 0.45:
                    print("[SEARCH] approach stopped — obstacle too close", flush=True)
                    break

                found, _ = self.target_visible(yolo_name)
                if not found:
                    break

                self.robot.move("forward", speed=0.16, duration=1.0)
                if not self._sleep_interruptible(1.1):
                    break
        finally:
            if hasattr(self.perceptions, "set_moving"):
                self.perceptions.set_moving(False)
            self.robot.stop()

    # ── Main search ──────────────────────────────────────────────

    def search(self, target_text: str, max_cycles=None, waypoints_per_cycle=12) -> Dict:
        print("=" * 50, flush=True)
        print(f"[SEARCH] STARTED: '{target_text}'", flush=True)
        print("=" * 50, flush=True)

        self._reset_search_memory(target_text)
        self._emit_status(target_text, "starting", message="Starting map search", force=True)

        # 1. Validate target text. Known YOLO classes use YOLO; other
        # object names are sent to the optional open-vocabulary detector.
        if not is_searchable_target(target_text):
            msg = (f"I cannot search for '{target_text}'. "
                   f"Please name a real visual target, like a person, door, key, cube, chair, or bottle.")
            self._emit_status(target_text, "rejected", found=False, message=msg, force=True)
            return {"found": False, "speech": msg, "where": "rejected"}

        yolo_name = resolve_yolo_name(target_text)
        detector_target = yolo_name or self._normalize_label(target_text)
        detector_type = "YOLO" if yolo_name else "open-vocabulary"
        print(f"[SEARCH] {detector_type} target: '{detector_target}'", flush=True)

        if hasattr(self.perceptions, "set_search_target"):
            self.perceptions.set_search_target(
                detector_target if yolo_name else target_text,
                open_vocab=(yolo_name is None),
            )

        self.robot.cancel_navigation()
        self.robot.stop()

        # 2. Check the current camera before any map/costmap work. If the
        # target is already visible, stop immediately and do not waste time
        # generating waypoints.
        if self.is_cancel_requested():
            return self._cancelled_result(target_text)

        self._emit_status(
            target_text, "checking_current_view", 0, 0,
            message="Checking the camera before moving", force=True
        )
        found, desc = self.target_visible(detector_target, force=True)
        if found:
            found, desc = self.target_visible_confirmed(
                detector_target, required_hits=1, max_reads=2, force=True
            )
        if self.is_cancel_requested():
            return self._cancelled_result(target_text)
        if found:
            return self._finish_found(
                detector_target, target_text, desc, "current view",
                f"I can already see the {target_text} right here! {desc}",
                0, 0
            )

        if hasattr(self.robot, "clear_costmaps"):
            self.robot.clear_costmaps()

        # 3. Build one full waypoint set for this search. Do not clear memory
        # during the search; when these points are exhausted, the map is done.
        spacing = float(os.environ.get("AI_SEARCH_SPACING_M", "1.00"))
        max_points = int(os.environ.get("AI_SEARCH_MAX_POINTS", "90"))
        clearance = float(os.environ.get("AI_SEARCH_CLEARANCE_M", "0.65"))
        all_waypoints = self.robot.get_search_waypoints(
            self.map_name, spacing_m=spacing, max_points=max_points,
            clearance_m=clearance,
        )
        self._all_waypoints = [dict(point) for point in all_waypoints]
        self._total_waypoints = len(all_waypoints)
        if not all_waypoints:
            msg = f"I have no map waypoints to search for {target_text}."
            self._emit_status(target_text, "not_found", found=False, message=msg, force=True)
            return {"found": False, "speech": msg, "where": "no waypoints"}

        pose = self.robot.get_amcl_pose()
        if pose and self.START_SCAN_SECONDS > 0.0:
            found, desc = self.scan_at_waypoint(
                detector_target, target_text, waypoint_index=0,
                waypoint_total=self._total_waypoints,
                turn_duration=self.START_SCAN_SECONDS
            )
            self._mark_visited(pose, scanned=True, note="start-position")
            if self.is_cancel_requested():
                return self._cancelled_result(target_text)
            if found:
                return self._finish_found(
                    detector_target, target_text, desc,
                    f"x={pose['x']:.2f} y={pose['y']:.2f}",
                    f"I found the {target_text}! {desc}",
                    0, self._total_waypoints
                )

        # 4. Exhaustive map search using Nearest Neighbor routing.
        consecutive_nav_failures = 0
        consecutive_stuck_goals = 0
        while True:
            if self.is_cancel_requested():
                return self._cancelled_result(target_text)

            # Find the closest unvisited waypoint from the current pose
            pose = self.robot.get_amcl_pose()
            
            # Filter unvisited waypoints
            unvisited = [wp for wp in all_waypoints if not self._already_visited(wp)]
            if not unvisited:
                break
                
            # If we don't have AMCL pose yet, default to first unvisited
            if pose is None:
                wp = unvisited[0]
                wp_dist = 0.0
            else:
                wp = min(unvisited, key=lambda p: math.hypot(p["x"] - pose["x"], p["y"] - pose["y"]))
                wp_dist = math.hypot(wp["x"] - pose["x"], wp["y"] - pose["y"])

            self._current_waypoint = {"x": float(wp["x"]), "y": float(wp["y"]), "yaw": float(wp.get("yaw", 0.0))}

            overall_idx = min(len(self._visited) + 1, self._total_waypoints)
            print(f"[SEARCH] wp {overall_idx}/{self._total_waypoints} "
                  f"x={wp['x']:.2f} y={wp['y']:.2f} d_from_robot={wp_dist:.2f}", flush=True)

            self._emit_status(
                target_text, "navigating", overall_idx, self._total_waypoints,
                message=f"Moving to search point {overall_idx}/{self._total_waypoints}",
                force=True
            )

            goal_started = self.robot.goto_map(wp["x"], wp["y"])
            if not goal_started:
                consecutive_nav_failures += 1
                self._mark_visited(wp, scanned=False, note="navigation-failed")
                self._emit_status(
                    target_text, "navigation_failed", overall_idx, self._total_waypoints,
                    message="Could not start navigation to this point", force=True
                )
                if consecutive_nav_failures >= 3 and hasattr(self.robot, "clear_costmaps"):
                    self.robot.clear_costmaps()
                    consecutive_nav_failures = 0
                continue
            consecutive_nav_failures = 0

            t_start       = time.time()
            best_d        = None
            last_progress = time.time()
            arrived       = False
            made_progress = False
            recovery_count = 0
            stuck_or_timeout = False

            try:
                if hasattr(self.perceptions, "set_moving"):
                    self.perceptions.set_moving(True)

                # Navigation poll loop. Vision stays live while move_base drives.
                while time.time() - t_start < self.NAV_TIMEOUT:
                    if self.is_cancel_requested():
                        return self._cancelled_result(target_text)

                    found, desc = self.target_visible(detector_target, force=False)
                    if self.is_cancel_requested():
                        return self._cancelled_result(target_text)
                    if found:
                        print(f"[SEARCH] live target hit while navigating: {desc}", flush=True)
                        self.robot.cancel_navigation()
                        self.robot.stop()
                        if hasattr(self.perceptions, "set_moving"):
                            self.perceptions.set_moving(False)
                        found_confirm, desc_confirm = self.target_visible_confirmed(
                            detector_target, required_hits=1, max_reads=2, force=True
                        )
                        if found_confirm:
                            return self._finish_found(
                                detector_target, target_text, desc_confirm,
                                f"x={wp['x']:.2f} y={wp['y']:.2f}",
                                f"I found the {target_text} while navigating! {desc_confirm}",
                                overall_idx, self._total_waypoints
                            )

                        print("[SEARCH] live hit was not confirmed; resuming waypoint", flush=True)
                        if self.is_cancel_requested():
                            return self._cancelled_result(target_text)
                        goal_started = self.robot.goto_map(wp["x"], wp["y"])
                        if not goal_started:
                            break
                        if hasattr(self.perceptions, "set_moving"):
                            self.perceptions.set_moving(True)
                        last_progress = time.time()

                    d = self.robot.distance_to(wp["x"], wp["y"])
                    distance_msg = "Navigation active"
                    if d is not None:
                        distance_msg = f"Moving. Distance to point: {d:.2f} m"
                        if d <= 0.55:
                            arrived = True
                            break   # arrived
                        if best_d is None:
                            best_d = d
                            last_progress = time.time()
                        elif d < best_d - self.PROGRESS_THRESH:
                            best_d        = d
                            last_progress = time.time()
                            made_progress = True
                        elif (
                            time.time() - t_start >= self.START_GRACE_SECONDS
                            and time.time() - last_progress > self.STUCK_TIMEOUT
                        ):
                            if recovery_count >= self.MAX_RECOVERIES_PER_WP:
                                print("[SEARCH] waypoint stuck — skipping without stopping search", flush=True)
                                self._emit_status(
                                    target_text, "navigation_stuck", overall_idx, self._total_waypoints,
                                    message="Waypoint skipped. Continuing search.", force=True
                                )
                                stuck_or_timeout = True
                                break
                            self._emit_status(
                                target_text, "recovery", overall_idx, self._total_waypoints,
                                message="Stuck. Trying recovery.", force=True
                            )
                            self._recovery_rotate()
                            recovery_count += 1
                            last_progress = time.time()
                            best_d = d
                    else:
                        last_progress = time.time()

                    self._emit_status(
                        target_text, "navigating", overall_idx, self._total_waypoints,
                        message=distance_msg
                    )
                    time.sleep(0.25)
                else:
                    stuck_or_timeout = True
            finally:
                if hasattr(self.perceptions, "set_moving"):
                    self.perceptions.set_moving(False)

            if arrived:
                consecutive_stuck_goals = 0
                self._mark_visited(wp, scanned=False, note="drive-by")
                continue

            if stuck_or_timeout and not self.STOP_AT_WAYPOINT:
                try:
                    self.robot.cancel_navigation()
                    self.robot.stop()
                except Exception:
                    pass
                if made_progress:
                    consecutive_stuck_goals = 0
                else:
                    consecutive_stuck_goals += 1
                    if consecutive_stuck_goals >= self.MAX_STUCK_GOALS:
                        msg = (
                            f"I could not make navigation progress after {consecutive_stuck_goals} goals. "
                            "The map search is stopped so the robot does not keep sending unreachable goals. "
                            "Check that move_base is publishing /cmd_vel and that AMCL pose is updating."
                        )
                        print(f"[SEARCH NAV ERROR] {msg}", flush=True)
                        self._emit_status(
                            target_text, "navigation_failed", overall_idx, self._total_waypoints,
                            found=False, message=msg, force=True, mode="not_found", can_talk=True
                        )
                        return {"found": False, "speech": msg, "where": "navigation failed"}
                    if consecutive_stuck_goals % 2 == 0 and hasattr(self.robot, "clear_costmaps"):
                        self.robot.clear_costmaps()
                self._mark_visited(wp, scanned=False, note="navigation-stuck")
                continue

            self.robot.cancel_navigation()
            self.robot.stop()
            should_scan = (
                self.STOP_AT_WAYPOINT
                and ((best_d is not None and best_d <= self.SCAN_IF_WITHIN) or made_progress)
            )
            if not should_scan:
                self._mark_visited(wp, scanned=False, note="navigation-stuck")
                continue

            self._mark_visited(wp, scanned=True, note="searched")

            found, desc = self.quick_check_at_waypoint(
                detector_target, target_text,
                waypoint_index=min(len(self._visited), self._total_waypoints),
                waypoint_total=self._total_waypoints
            )
            if self.is_cancel_requested():
                return self._cancelled_result(target_text)
            if found:
                return self._finish_found(
                    detector_target, target_text, desc,
                    f"x={wp['x']:.2f} y={wp['y']:.2f}",
                    f"I found the {target_text}! {desc}",
                    min(len(self._visited), self._total_waypoints),
                    self._total_waypoints
                )

            full_scan_every = max(0, int(self.FULL_SCAN_EVERY))
            should_full_scan = full_scan_every > 0 and len(self._visited) % full_scan_every == 0
            if not should_full_scan:
                continue

            found, desc = self.scan_at_waypoint(
                detector_target, target_text,
                waypoint_index=min(len(self._visited), self._total_waypoints),
                waypoint_total=self._total_waypoints
            )
            if self.is_cancel_requested():
                return self._cancelled_result(target_text)
            if found:
                return self._finish_found(
                    detector_target, target_text, desc,
                    f"x={wp['x']:.2f} y={wp['y']:.2f}",
                    f"I found the {target_text}! {desc}",
                    min(len(self._visited), self._total_waypoints),
                    self._total_waypoints
                )

        self.robot.cancel_navigation()
        self.robot.stop()
        display_searched = len(self._visited)
        if self._total_waypoints > 0:
            display_searched = min(display_searched, self._total_waypoints)

        if max_cycles is None:
            speech = (f"I searched the map for {target_text}, "
                      f"including {display_searched} places, but I did not find it.")
            where = "map exhausted"
        else:
            speech = (f"I searched {display_searched} places for {target_text}, "
                      f"but I did not find it before the search limit.")
            where = "search limit"

        self._emit_status(
            target_text, "not_found", found=False, message=speech,
            force=True, mode="not_found", can_talk=True
        )
        return {"found": False, "speech": speech, "where": where}
