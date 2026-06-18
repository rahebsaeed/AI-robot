import time
import math
import re
from typing import Dict, List, Tuple

import cv2
import numpy as np


YOLO_ALIASES = {
    "person": ["person", "human", "man", "woman", "boy", "girl"],
    "chair": ["chair", "seat"],
    "tv": ["tv", "television", "screen", "monitor"],
    "laptop": ["laptop", "computer"],
    "bottle": ["bottle"],
    "cup": ["cup", "mug"],
    "book": ["book"],
    "cell phone": ["phone", "mobile", "cell phone"],
    "backpack": ["backpack", "bag"],
    "skateboard": ["skateboard"],
}


COLOR_RANGES = {
    "red": [
        ((0, 80, 60), (10, 255, 255)),
        ((170, 80, 60), (180, 255, 255)),
    ],
    "blue": [((90, 80, 50), (130, 255, 255))],
    "green": [((35, 60, 50), (85, 255, 255))],
    "yellow": [((20, 80, 80), (35, 255, 255))],
    "orange": [((10, 80, 80), (22, 255, 255))],
    "white": [((0, 0, 180), (180, 60, 255))],
    "black": [((0, 0, 0), (180, 255, 60))],
}

SHAPE_WORDS = [
    "cube", "box", "ball", "circle", "square", "key",
    "object", "marker", "toy"
]


def normalize_command_text(text: str) -> str:
    """
    Normalize common Whisper mistakes before command classification.
    Examples:
      'for in the person' -> 'find the person'
      'end the person'    -> 'find the person'
      'fine the chair'    -> 'find the chair'
    """
    t = str(text).lower().strip()

    # Remove punctuation noise
    t = t.replace(".", " ").replace("?", " ").replace(",", " ")
    t = re.sub(r"\s+", " ", t).strip()

    replacements = {
        "for in the ": "find the ",
        "for in a ": "find a ",
        "for in an ": "find an ",
        "for in ": "find ",
        "end the ": "find the ",
        "end a ": "find a ",
        "end an ": "find an ",
        "end ": "find ",
        "fine the ": "find the ",
        "fine a ": "find a ",
        "fine an ": "find an ",
        "fine ": "find ",
        "fined the ": "find the ",
        "fund the ": "find the ",
        "found the ": "find the ",
        "foreign the ": "find the ",
        "phone the ": "find the ",
    }

    for bad, good in replacements.items():
        if t.startswith(bad):
            t = good + t[len(bad):]
            break

    t = re.sub(r"\s+", " ", t).strip()
    return t


def is_search_request(text: str) -> bool:
    t = normalize_command_text(text)

    patterns = [
        "search for",
        "look for",
        "find",
        "where is",
        "where are",
        "locate",
        "go find",
    ]

    return any(p in t for p in patterns)


def parse_search_target(text: str) -> str:
    t = normalize_command_text(text)

    remove_phrases = [
        "can you", "could you", "please",
        "search for", "look for", "go find",
        "find", "where is", "where are", "locate",
        "the ", "a ", "an ",
        "?", "."
    ]

    for ptn in remove_phrases:
        t = t.replace(ptn, " ")

    t = re.sub(r"\s+", " ", t).strip()

    return t or "object"



def normalize_target(target_text: str) -> Dict:
    t = target_text.lower().strip()

    color = None
    for c in COLOR_RANGES:
        if c in t:
            color = c
            break

    yolo_name = None
    for canonical, aliases in YOLO_ALIASES.items():
        if canonical in t or any(a in t for a in aliases):
            yolo_name = canonical
            break

    shape = None
    for s in SHAPE_WORDS:
        if s in t:
            shape = s
            break

    return {
        "raw": target_text,
        "color": color,
        "yolo_name": yolo_name,
        "shape": shape,
    }


class MapSearchTask:
    """
    Map-based generic visual search.

    Uses:
      - AMCL pose
      - saved map free cells
      - move_base goals
      - RGB camera / YOLO / color-shape detector

    It does not only turn in place.
    It navigates between map waypoints and checks the camera while moving.
    """

    def __init__(self, robot, perceptions, map_name="salle_robotique"):
        self.robot = robot
        self.perceptions = perceptions
        self.map_name = map_name
        self.visited = []
        self.visit_radius = 0.75

    def _distance(self, a, b):
        return math.hypot(float(a["x"]) - float(b["x"]), float(a["y"]) - float(b["y"]))

    def _already_visited(self, point):
        for v in self.visited:
            if self._distance(point, v) < self.visit_radius:
                return True
        return False

    def _mark_visited(self, point):
        self.visited.append({
            "x": float(point["x"]),
            "y": float(point["y"]),
            "time": time.time()
        })

    def _get_latest_frame(self):
        for name in ["latest_frame", "last_frame", "frame", "_latest_frame"]:
            if hasattr(self.perceptions, name):
                frame = getattr(self.perceptions, name)
                if frame is not None:
                    return frame
        return None

    def _detect_colored_target(self, frame, target: Dict) -> Tuple[bool, str, float]:
        color = target.get("color")
        shape = target.get("shape")

        if color is None or color not in COLOR_RANGES:
            return False, "", 0.0

        hsv = cv2.cvtColor(frame, cv2.COLOR_BGR2HSV)

        mask = None

        for low, high in COLOR_RANGES[color]:
            part = cv2.inRange(
                hsv,
                np.array(low, dtype=np.uint8),
                np.array(high, dtype=np.uint8)
            )
            mask = part if mask is None else cv2.bitwise_or(mask, part)

        kernel = np.ones((5, 5), np.uint8)
        mask = cv2.morphologyEx(mask, cv2.MORPH_OPEN, kernel)
        mask = cv2.morphologyEx(mask, cv2.MORPH_CLOSE, kernel)

        contours, _ = cv2.findContours(mask, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)

        best_area = 0
        best_desc = ""

        for cnt in contours:
            area = cv2.contourArea(cnt)

            if area < 250:
                continue

            x, y, w, h = cv2.boundingRect(cnt)

            if w <= 0 or h <= 0:
                continue

            aspect = w / float(h)
            peri = cv2.arcLength(cnt, True)
            approx = cv2.approxPolyDP(cnt, 0.04 * peri, True)

            shape_ok = True

            if shape in ["cube", "box", "square", "key"]:
                shape_ok = 3 <= len(approx) <= 8 and 0.25 <= aspect <= 4.0
            elif shape in ["ball", "circle"]:
                shape_ok = len(approx) >= 6
            else:
                shape_ok = True

            if not shape_ok:
                continue

            if area > best_area:
                best_area = area
                best_desc = f"{color} {shape or 'object'} detected, area={int(area)}, box=({x},{y},{w},{h})"

        if best_area > 0:
            return True, best_desc, best_area

        return False, "", 0.0

    def target_visible(self, target_text: str) -> Tuple[bool, str]:
        """
        Professional target check.

        Uses multiple fresh perception reads because one YOLO frame can miss a person,
        especially with 320x240 camera frames or partial body view.
        """

        target = normalize_target(target_text)
        yolo_name = target.get("yolo_name")

        # For person search, accept lower confidence because the head camera stream is small.
        if yolo_name == "person":
            min_conf = 0.16
            reads = 5
        else:
            min_conf = 0.25
            reads = 3

        best_seen = []

        for k in range(reads):
            try:
                vision = self.perceptions.see()
            except Exception as e:
                vision = {}
                print(f"[SEARCH VISION ERROR]: {e}", flush=True)

            if isinstance(vision, dict):
                objects = vision.get("objects", [])
                desc = vision.get("description", "")
            else:
                objects = []
                desc = ""

            if objects:
                best_seen = objects

            # Always print what the search detector sees.
            if objects:
                compact = [
                    f"{o.get('name')}:{float(o.get('confidence', 0.0)):.2f}:{o.get('position', '?')}"
                    for o in objects
                ]
                print(f"[SEARCH VISION] frame_check={k+1}/{reads} objects={compact}", flush=True)
            else:
                print(f"[SEARCH VISION] frame_check={k+1}/{reads} no objects. desc={desc}", flush=True)

            if yolo_name:
                for obj in objects:
                    name = str(obj.get("name", "")).lower()
                    conf = float(obj.get("confidence", 0.0))
                    pos = str(obj.get("position", "unknown"))
                    dist = str(obj.get("distance_hint", "unknown"))

                    if name == yolo_name and conf >= min_conf:
                        return True, f"found {yolo_name}, confidence={conf:.2f}, position={pos}, distance={dist}"

            time.sleep(0.20)

        # Color/custom target fallback
        frame = self._get_latest_frame()

        if frame is not None:
            found, desc, area = self._detect_colored_target(frame, target)
            if found:
                return True, desc

        if best_seen:
            return False, f"{target_text} not found. YOLO saw: {best_seen}"

        return False, f"{target_text} not visible"

    def choose_waypoints(self, max_points=20) -> List[Dict]:
        pose = self.robot.get_amcl_pose()
        points = self.robot.get_search_waypoints(self.map_name, spacing_m=0.9, max_points=120)

        if not points:
            print("[SEARCH] no generated map waypoints", flush=True)
            return []

        if pose is None:
            print("[SEARCH] AMCL pose unavailable, using raw waypoint order", flush=True)
            base = {"x": points[0]["x"], "y": points[0]["y"]}
        else:
            base = pose
            print(f"[SEARCH] AMCL pose x={pose['x']:.2f}, y={pose['y']:.2f}, yaw={pose['yaw']:.2f}", flush=True)

        candidates = []

        for p in points:
            if self._already_visited(p):
                continue

            d = math.hypot(float(p["x"]) - float(base["x"]), float(p["y"]) - float(base["y"]))

            # Avoid selecting the exact current spot repeatedly.
            if d < 0.6:
                continue

            p2 = dict(p)
            p2["distance"] = d
            candidates.append(p2)

        candidates.sort(key=lambda z: z["distance"])

        return candidates[:max_points]

    def scan_at_current_place(self, target_text: str, seconds=8.0) -> Tuple[bool, str]:
        """
        Professional local search:
        stop at the waypoint and analyze camera frames for several seconds.

        No fake UDP turning.
        """

        print(f"[SEARCH] analyzing camera at waypoint for {target_text}", flush=True)

        self.robot.stop()

        start = time.time()
        checks = 0

        while time.time() - start < seconds:
            checks += 1
            found, desc = self.target_visible(target_text)

            if found:
                print(f"[SEARCH] TARGET FOUND at waypoint: {desc}", flush=True)
                self.robot.cancel_navigation()
                self.robot.stop()
                return True, desc

            time.sleep(0.5)

        print(f"[SEARCH] target not found after {checks} camera checks at this waypoint", flush=True)
        return False, "not found in waypoint camera analysis"

    def approach_if_visible(self, target_text: str, max_seconds=8.0) -> Tuple[bool, str]:
        """
        Simple cautious approach after target is detected.
        Uses lidar safety from main/perceptions indirectly through robot bridge.
        """
        print(f"[SEARCH] target visible, cautious approach: {target_text}", flush=True)

        start = time.time()

        while time.time() - start < max_seconds:
            found, desc = self.target_visible(target_text)

            if not found:
                self.robot.stop()
                return False, "target lost during approach"

            # Short forward pulses. Main lidar safety is not here, so keep speed small.
            self.robot.move("forward", speed=0.18, duration=1.2)
            time.sleep(1.4)

        self.robot.stop()
        return True, "stopped near visible target"

    def search(self, target_text: str, max_cycles=5, waypoints_per_cycle=12) -> Dict:
        print("========================================", flush=True)
        print(f"[SEARCH] MAP NAVIGATION SEARCH STARTED: {target_text}", flush=True)
        print("========================================", flush=True)

        self.robot.cancel_navigation()
        self.robot.stop()

        # Check current view first.
        found, desc = self.target_visible(target_text)

        if found:
            self.approach_if_visible(target_text)
            return {
                "found": True,
                "speech": f"I found {target_text} and stopped near it. {desc}",
                "where": "current camera view"
            }

        for cycle in range(1, max_cycles + 1):
            print(f"[SEARCH] cycle {cycle}/{max_cycles}", flush=True)

            waypoints = self.choose_waypoints(max_points=waypoints_per_cycle)

            if not waypoints:
                print("[SEARCH] no new waypoints, clearing visited memory and trying again", flush=True)
                self.visited.clear()
                waypoints = self.choose_waypoints(max_points=waypoints_per_cycle)

            if not waypoints:
                return {
                    "found": False,
                    "speech": f"I could not create map waypoints to search for {target_text}.",
                    "where": "no waypoints"
                }

            for i, wp in enumerate(waypoints, start=1):
                print(
                    f"[SEARCH] going to waypoint {i}/{len(waypoints)} "
                    f"x={wp['x']:.2f}, y={wp['y']:.2f}, d={wp.get('distance', 0):.2f}",
                    flush=True
                )

                self.robot.goto_map(wp["x"], wp["y"], wp.get("yaw", 0.0))

                move_start = time.time()
                arrived = False

                best_distance = None
                last_progress_time = time.time()
                stuck_limit_seconds = 10.0

                while time.time() - move_start < 45.0:
                    found, desc = self.target_visible(target_text)

                    if found:
                        self.robot.cancel_navigation()
                        self.approach_if_visible(target_text)
                        return {
                            "found": True,
                            "speech": f"I found {target_text} while navigating and stopped near it. {desc}",
                            "where": f"x={wp['x']:.2f}, y={wp['y']:.2f}"
                        }

                    d = self.robot.distance_to(wp["x"], wp["y"])

                    if d is not None:
                        print(f"[SEARCH] distance to waypoint: {d:.2f} m", flush=True)

                        if d <= 0.55:
                            arrived = True
                            break

                        # AI-style progress decision:
                        # If distance is improving, continue.
                        # If distance is not improving for several seconds, this waypoint is likely blocked.
                        if best_distance is None or d < best_distance - 0.08:
                            best_distance = d
                            last_progress_time = time.time()
                            print(f"[AI DECISION]: progress is good, continue to waypoint. best_distance={best_distance:.2f}", flush=True)
                        elif time.time() - last_progress_time > stuck_limit_seconds:
                            print(
                                f"[AI DECISION]: no progress for {stuck_limit_seconds:.0f}s. "
                                f"Waypoint may be blocked or unreachable. Skipping to another place.",
                                flush=True
                            )
                            break

                    time.sleep(1.0)

                self.robot.cancel_navigation()
                self.robot.stop()
                self._mark_visited(wp)

                if arrived:
                    print("[SEARCH] arrived at waypoint, scanning camera", flush=True)
                else:
                    print("[SEARCH] waypoint timeout, scanning anyway then continuing", flush=True)

                found, desc = self.scan_at_current_place(target_text, seconds=7.0)

                if found:
                    self.approach_if_visible(target_text)
                    return {
                        "found": True,
                        "speech": f"I found {target_text} and stopped near it. {desc}",
                        "where": f"x={wp['x']:.2f}, y={wp['y']:.2f}"
                    }

        # After many cycles, stop safely and report.
        self.robot.cancel_navigation()
        self.robot.stop()

        return {
            "found": False,
            "speech": f"I searched many map positions but I did not find {target_text}.",
            "where": "all planned search cycles"
        }
