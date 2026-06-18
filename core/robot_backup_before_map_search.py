import socket
import json
import subprocess
import threading
import time


class Robot:
    """
    Fast robot command interface.

    Sends movement commands to Docker ROS command bridge on UDP port 5020.

    Important:
    - Normal move commands keep sending for a useful duration.
    - arm='searching' starts a camera search scan.
    - stop() cancels any active search/movement immediately.
    """

    def __init__(self, port=5020):
        self.port = port
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

        except Exception:
            pass

        self._move_thread = None
        self._search_thread = None
        self._stop_event = threading.Event()
        self._lock = threading.Lock()

        print(
            f"[ROBOT] Fast UDP ROS command mode active. Targets={self.targets}, port={self.port}",
            flush=True
        )

    def _send_once(self, payload):
        data = json.dumps(payload).encode("utf-8")

        for host in self.targets:
            try:
                self.sock.sendto(data, (host, self.port))
                print(f"[ROBOT UDP SENT]: {host}:{self.port} {payload}", flush=True)
            except Exception as e:
                print(f"[ROBOT UDP ERROR] target={host}:{self.port} error={e}", flush=True)

    def _send_move(self, direction, speed):
        payload = {
            "type": "move",
            "direction": direction,
            "speed": float(speed)
        }
        self._send_once(payload)

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

        # Better useful durations.
        # Forward should be long enough to actually travel when clear.
        if duration is None:
            if direction == "stop":
                duration = 0.0
            elif direction in ["left", "right"]:
                duration = 5.0
            elif direction == "forward":
                duration = 10.0
            else:
                duration = 6.0

        print(
            f"[ROBOT UDP COMMAND]: direction={direction}, speed={speed}, duration={duration}",
            flush=True
        )

        with self._lock:
            self._stop_event.set()

            if self._move_thread is not None and self._move_thread.is_alive():
                self._move_thread.join(timeout=0.4)

            if self._search_thread is not None and self._search_thread.is_alive():
                self._search_thread.join(timeout=0.4)

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

    def _search_scan_worker(self):
        """
        Simple active search scan.

        This is triggered when main.py sends arm='searching'.
        It rotates the robot slowly so the camera can see more of the room.

        Later, main.py/perception should stop this scan when red cube is detected.
        """

        print("[ROBOT SEARCH]: active scan started", flush=True)

        # Phase 1: rotate right slowly
        start = time.time()
        while not self._stop_event.is_set() and time.time() - start < 12.0:
            self._send_move("right", 0.35)
            time.sleep(0.20)

        # Short stop
        self._send_move("stop", 0.0)
        time.sleep(0.5)

        # Phase 2: rotate left slowly, covering missed side
        start = time.time()
        while not self._stop_event.is_set() and time.time() - start < 12.0:
            self._send_move("left", 0.35)
            time.sleep(0.20)

        self._send_move("stop", 0.0)
        print("[ROBOT SEARCH]: active scan finished", flush=True)

    def start_search_scan(self):
        with self._lock:
            self._stop_event.set()

            if self._move_thread is not None and self._move_thread.is_alive():
                self._move_thread.join(timeout=0.4)

            if self._search_thread is not None and self._search_thread.is_alive():
                self._search_thread.join(timeout=0.4)

            self._stop_event.clear()

            self._search_thread = threading.Thread(
                target=self._search_scan_worker,
                daemon=True
            )
            self._search_thread.start()

    def control_arm(self, command="home"):
        command = str(command).strip().lower()

        valid = ["searching", "pickup", "drop", "home", "hand", "wave"]

        if command not in valid:
            command = "home"

        print(f"[ROBOT ARM]: requested={command}", flush=True)

        # Important fix:
        # main.py currently sends arm='searching' for search tasks.
        # Convert that into real search motion.
        if command == "searching":
            self.start_search_scan()

        payload = {
            "type": "arm",
            "command": command
        }

        print(f"[ROBOT ARM UDP]: {payload}", flush=True)
        self._send_once(payload)

    def stop(self):
        with self._lock:
            self._stop_event.set()

        self._send_move("stop", 0.0)
