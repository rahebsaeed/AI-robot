import os
import sys
import json
import re
import cv2
import time
import signal
import subprocess

from core.brain import Brain
from core.perceptions import Perceptions
from core.robot import Robot
from core.search_tasks import MapSearchTask, is_search_request, parse_search_target
from core.face_bridge import FaceBridge


OBSTACLE_STOP_DISTANCE = 0.45
OBSTACLE_SLOW_DISTANCE = 0.80

DEFAULT_FORWARD_SPEED = 0.25
DEFAULT_BACKWARD_SPEED = 0.20
DEFAULT_TURN_SPEED = 0.22

MAX_SAFE_SPEED = 0.35
PERSON_FORWARD_SPEED = 0.15


def start_robot_face():
    project_dir = os.path.dirname(os.path.abspath(__file__))
    face_file = os.path.join(project_dir, "robot_face.py")
    log_file = os.path.join(project_dir, "face_interface.log")

    if not os.path.exists(face_file):
        print(f"[FACE ERROR] robot_face.py not found at: {face_file}", flush=True)
        return None

    print("[FACE] Starting robot face interface...", flush=True)

    try:
        env = os.environ.copy()
        env["QT_QPA_PLATFORM"] = "xcb"
        env["DISPLAY"] = env.get("DISPLAY", ":0")

        xauth = os.path.expanduser("~/.Xauthority")
        if os.path.exists(xauth):
            env["XAUTHORITY"] = xauth

        qt_plugins = "/usr/lib/aarch64-linux-gnu/qt5/plugins"
        env["QT_PLUGIN_PATH"] = qt_plugins
        env["QT_QPA_PLATFORM_PLUGIN_PATH"] = os.path.join(qt_plugins, "platforms")

        print(f"[FACE] Using display: {env.get('DISPLAY')}", flush=True)
        print("[FACE] Using Qt platform: xcb", flush=True)
        print(f"[FACE] Using Qt plugins: {qt_plugins}", flush=True)

        log = open(log_file, "w")

        process = subprocess.Popen(
            [sys.executable, face_file],
            cwd=project_dir,
            stdout=log,
            stderr=log,
            env=env,
            start_new_session=True
        )

        time.sleep(3)

        if process.poll() is not None:
            print("[FACE ERROR] robot_face.py started but crashed.", flush=True)
            print(f"[FACE ERROR] Check log file: {log_file}", flush=True)
            return None

        print("[FACE] Robot face interface is running.", flush=True)
        return process

    except Exception as e:
        print(f"[FACE ERROR] Could not start robot face: {e}", flush=True)
        return None


def stop_robot_face(process):
    if process is None:
        return

    print("[FACE] Closing robot face interface...", flush=True)

    try:
        process.terminate()
        process.wait(timeout=3)
    except Exception:
        try:
            os.killpg(os.getpgid(process.pid), signal.SIGTERM)
        except Exception:
            pass


def extract_first_json_object(text):
    if not text:
        return None

    decoder = json.JSONDecoder()

    for i, ch in enumerate(text):
        if ch == "{":
            try:
                obj, _ = decoder.raw_decode(text[i:])
                if isinstance(obj, dict):
                    return obj
            except Exception:
                continue

    return None


def fallback_model_data(text):
    clean = str(text).strip()

    if not clean:
        clean = "I am ready."

    return {
        "thought": "Plain text response converted to safe robot action.",
        "speech": clean,
        "action": {
            "move": "stop",
            "speed": 0.0,
            "arm": "home"
        }
    }


def get_lidar_distance(perceptions):
    try:
        if hasattr(perceptions, "get_lidar_distance"):
            return perceptions.get_lidar_distance()
    except Exception:
        pass

    try:
        if hasattr(perceptions, "min_lidar_distance"):
            return perceptions.min_lidar_distance
    except Exception:
        pass

    return None


def get_objects_from_vision(vision_data):
    if isinstance(vision_data, dict):
        objects = vision_data.get("objects", [])
        return objects if isinstance(objects, list) else []

    if isinstance(vision_data, list):
        return vision_data

    return []


def get_vision_description(vision_data):
    if isinstance(vision_data, dict):
        return vision_data.get("description", "No camera description available.")

    if isinstance(vision_data, list):
        if vision_data:
            return "Detected objects: " + ", ".join(str(x) for x in vision_data)
        return "No specific objects detected."

    return "No camera description available."


def has_movement_verb(text):
    """
    Movement must contain a real command verb.
    This prevents accidental movement from phrases like:
    'back tomorrow', 'thank you', 'come back later'.
    """
    movement_verbs = [
        "move",
        "go",
        "turn",
        "drive",
        "walk",
        "rotate",
        "advance",
        "reverse",
        "stop",
        "halt",
        "freeze",
        "approach",
        "follow"
    ]

    return any(re.search(r"\b" + re.escape(v) + r"\b", text) for v in movement_verbs)


def detect_movement_intent(command, objects):
    text = command.lower().strip()
    objects_lower = [str(obj).lower() for obj in objects] if objects else []

    # Direct stop is always allowed
    if re.search(r"\b(stop|halt|freeze)\b", text) or "don't move" in text or "do not move" in text:
        return "stop"

    # Require a real movement verb for all other movement.
    # Exception: short direct commands like "left", "right", "forward".
    short_direct = text in ["left", "right", "forward", "backward", "back", "ahead", "go ahead"]

    if not has_movement_verb(text) and not short_direct:
        return None

    if re.search(r"\b(forward|ahead|straight)\b", text) or "go ahead" in text:
        return "forward"

    if re.search(r"\b(backward|reverse)\b", text) or "move back" in text or "go back" in text:
        return "backward"

    # Do NOT treat "back tomorrow" as backward
    if text in ["back", "move back", "go back"]:
        return "backward"

    if re.search(r"\bleft\b", text):
        return "left"

    if re.search(r"\bright\b", text):
        return "right"

    if any(phrase in text for phrase in [
        "move to the person",
        "go to the person",
        "approach the person",
        "follow the person",
        "go near the person"
    ]):
        if "person" in objects_lower:
            return "approach_person"
        return "search_person"

    return None


def detect_arm_intent(command):
    text = command.lower()

    if any(word in text for word in ["drop", "release", "put down"]):
        return "drop"

    if any(word in text for word in ["pick", "pickup", "pick up", "grab", "take", "hold"]):
        return "pickup"

    if any(word in text for word in ["search", "find", "look for", "scan for"]):
        return "searching"

    return None


def normalize_model_data(data):
    if not isinstance(data, dict):
        data = fallback_model_data("I am ready.")

    speech = str(data.get("speech", "I am ready."))
    thought = str(data.get("thought", data.get("think", "No thought.")))

    action = data.get("action", {})
    if not isinstance(action, dict):
        action = {}

    return {
        "thought": thought,
        "speech": speech,
        "action": {
            "move": "stop",
            "speed": 0.0,
            "arm": "home"
        }
    }


def build_safe_action(command, model_data, vision_data, lidar_distance):
    data = normalize_model_data(model_data)

    objects = get_objects_from_vision(vision_data)
    vision_description = get_vision_description(vision_data)

    movement_intent = detect_movement_intent(command, objects)
    arm_intent = detect_arm_intent(command)

    move = "stop"
    speed = 0.0
    arm = "home"

    if movement_intent == "forward":
        move = "forward"
        speed = DEFAULT_FORWARD_SPEED

    elif movement_intent == "backward":
        move = "backward"
        speed = DEFAULT_BACKWARD_SPEED

    elif movement_intent == "left":
        move = "left"
        speed = DEFAULT_TURN_SPEED

    elif movement_intent == "right":
        move = "right"
        speed = DEFAULT_TURN_SPEED

    elif movement_intent == "stop":
        move = "stop"
        speed = 0.0
        data["speech"] = "Stopping now."

    elif movement_intent == "approach_person":
        if lidar_distance is None:
            move = "left"
            speed = 0.15
            data["speech"] = "I cannot safely drive forward because lidar is not ready, so I will turn slowly instead."
        else:
            move = "forward"
            speed = PERSON_FORWARD_SPEED
            data["speech"] = "I will approach very slowly."

    elif movement_intent == "search_person":
        move = "left"
        speed = 0.15
        data["speech"] = "I do not recognize a person yet, so I will turn slowly to search."

    vision_question_phrases = [
        "what you are seeing",
        "what are you seeing",
        "what do you see",
        "what do i see",
        "what can you see",
        "camera",
        "visible",
        "detect"
    ]

    if any(phrase in command.lower() for phrase in vision_question_phrases):
        move = "stop"
        speed = 0.0
        arm = "home"
        data["speech"] = vision_description

    if arm_intent:
        arm = arm_intent
    else:
        arm = "home"

    if move == "forward":
        if lidar_distance is None:
            move = "stop"
            speed = 0.0
            data["speech"] = "I cannot move forward because lidar is not ready."

        else:
            try:
                d = float(lidar_distance)

                if d < OBSTACLE_STOP_DISTANCE:
                    move = "stop"
                    speed = 0.0
                    data["speech"] = "Obstacle detected in front. I will not move forward."

                elif d < OBSTACLE_SLOW_DISTANCE:
                    speed = min(speed, 0.12)
                    data["speech"] = "I will move forward slowly because something is nearby."

            except Exception:
                move = "stop"
                speed = 0.0
                data["speech"] = "I cannot move forward because lidar data is invalid."

    if move != "stop":
        speed = max(0.10, min(speed, MAX_SAFE_SPEED))
    else:
        speed = 0.0

    data["action"] = {
        "move": move,
        "speed": speed,
        "arm": arm
    }

    return data


def show_conversation_on_face(face, user_text, ai_text):
    try:
        message = f"User: {user_text}\nAI: {ai_text}"
        face.show_ai_response(message)
    except Exception as e:
        print(f"[FACE MESSAGE ERROR] {e}", flush=True)


def main():
    face_process = start_robot_face()

    brain = Brain(model_name="Qwen/Qwen2.5-1.5B-Instruct")
    perceptions = Perceptions()
    robot = Robot()
    search_task = MapSearchTask(robot, perceptions, map_name=os.environ.get("AI_MAP_NAME", "salle_robotique"))
    face = FaceBridge()

    print("--- 1.5B SYSTEM ACTIVE ---", flush=True)

    face.send_emotion("happy")
    face.send_message("AI companion system is active and ready.")

    try:
        while True:
            frame, vision_data = perceptions.see()
            lidar_distance = get_lidar_distance(perceptions)

            command = perceptions.listen()

            if command:
                print(f"User: {command}", flush=True)
                # ==================================================
                # HARD PRIORITY TASK HANDLER
                # Search/find/look-for commands must NOT go to Qwen.
                # They must use AMCL + map + move_base + camera.
                # ==================================================
                if is_search_request(command):
                    target = parse_search_target(command)

                    print("========================================", flush=True)
                    print(f"[TASK] MAP SEARCH REQUEST: {target}", flush=True)
                    print("========================================", flush=True)

                    try:
                        perceptions.speak(f"I will search the map for {target}.")
                    except Exception:
                        pass

                    result = search_task.search(target)

                    print(f"[TASK RESULT]: {result}", flush=True)

                    speech = result.get("speech", f"I finished searching for {target}.")

                    try:
                        perceptions.speak(speech)
                    except Exception:
                        pass

                    # Do not let Qwen convert this to arm=searching.
                    continue

                try:
                    face.send_emotion("thinking")
                    face.send_message(f"User: {command}")
                except Exception:
                    pass

                raw_response, latency = brain.process_command(
                    command,
                    vision_data=vision_data,
                    lidar_distance=lidar_distance
                )

                try:
                    model_data = extract_first_json_object(raw_response)

                    if model_data is None:
                        model_data = fallback_model_data(raw_response)

                    safe_data = build_safe_action(
                        command=command,
                        model_data=model_data,
                        vision_data=vision_data,
                        lidar_distance=lidar_distance
                    )

                    speech = safe_data.get("speech", "")
                    action = safe_data.get("action", {})

                    move_cmd = action.get("move", "stop")
                    speed = action.get("speed", 0.0)
                    arm_cmd = action.get("arm", "home")

                    print(f"AI Response: {speech}", flush=True)
                    print(f"Safe Action: move={move_cmd}, speed={speed}, arm={arm_cmd}", flush=True)
                    print(f"Vision Data: {vision_data}", flush=True)
                    print(f"Lidar Distance: {lidar_distance}", flush=True)
                    print(f"Latency: {latency:.2f}s", flush=True)

                    show_conversation_on_face(face, command, speech)
                    perceptions.speak(speech)

                    robot.move(move_cmd, speed)
                    robot.control_arm(arm_cmd)

                except Exception as e:
                    print(f"Processing Error: {e} | Raw: {raw_response}", flush=True)

                    face.send_emotion("sad")
                    face.send_message("I had a problem processing this command.")

                    robot.move("stop", 0.0)
                    robot.control_arm("home")

            if cv2.waitKey(1) & 0xFF == ord("q"):
                break

    except KeyboardInterrupt:
        print("Stopping AI system...", flush=True)

    finally:
        try:
            face.send_emotion("neutral")
            face.send_message("AI companion system stopped.")
            robot.move("stop", 0.0)
        except Exception:
            pass

        stop_robot_face(face_process)


if __name__ == "__main__":
    main()
