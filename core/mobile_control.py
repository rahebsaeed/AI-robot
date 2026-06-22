import threading
import time


class MobileTeleopController:
    """Dead-man teleoperation controller. Motion stops when heartbeats expire."""

    VALID_DIRECTIONS = {"forward", "backward", "left", "right"}

    def __init__(self, robot, search_task, status_callback=None,
                 preempt_callback=None, timeout=0.65):
        self.robot = robot
        self.search_task = search_task
        self.status_callback = status_callback
        self.preempt_callback = preempt_callback
        self.timeout = max(0.35, float(timeout))
        self._lock = threading.Lock()
        self._direction = "stop"
        self._speed = 0.0
        self._deadline = 0.0
        self._active = False
        self._closed = threading.Event()
        self._cancel_started = False
        self._thread = threading.Thread(target=self._watchdog, name="mobile-teleop", daemon=True)
        self._thread.start()

    def update(self, direction: str, speed: float, active: bool = True, client_id: str = "") -> bool:
        direction = str(direction or "stop").lower().strip()
        try:
            speed = float(speed)
        except (TypeError, ValueError):
            speed = 0.0
        speed = max(0.0, min(1.0, speed))

        if not active or direction == "stop":
            self.stop(reason="released")
            return True
        if direction not in self.VALID_DIRECTIONS or speed <= 0.0:
            return False

        should_cancel_navigation = False
        with self._lock:
            if not self._active and not self._cancel_started:
                self._cancel_started = True
                should_cancel_navigation = True
            self._direction = direction
            self._speed = speed
            self._deadline = time.monotonic() + self.timeout
            self._active = True

        if should_cancel_navigation:
            if self.preempt_callback is not None:
                try:
                    self.preempt_callback()
                except Exception as exc:
                    print(f"[MOBILE TELEOP PREEMPT ERROR] {exc}", flush=True)
            self.search_task.request_cancel()
            self.robot.stop()
            threading.Thread(target=self._cancel_navigation, daemon=True).start()
            self._emit("manual", f"Manual control active: {direction}")
        return True

    def _cancel_navigation(self):
        try:
            self.robot.cancel_navigation()
        finally:
            with self._lock:
                self._cancel_started = False

    def stop(self, reason="stopped"):
        with self._lock:
            was_active = self._active
            self._active = False
            self._direction = "stop"
            self._speed = 0.0
            self._deadline = 0.0
        if was_active:
            self.robot.stop()
            self._emit("manual_stopped", f"Manual control {reason}")

    def close(self):
        self._closed.set()
        self.stop(reason="closed")

    def _watchdog(self):
        last_send = 0.0
        while not self._closed.wait(0.05):
            with self._lock:
                active = self._active
                direction = self._direction
                speed = self._speed
                deadline = self._deadline

            now = time.monotonic()
            if not active:
                continue
            if now >= deadline:
                self.stop(reason="timed out")
                continue
            if now - last_send >= 0.18:
                self.robot.send_manual_velocity(direction, speed)
                last_send = now

    def _emit(self, phase: str, message: str):
        if self.status_callback is None:
            return
        try:
            self.status_callback({
                "mode": "manual" if phase == "manual" else "ready",
                "phase": phase,
                "message": message,
                "can_talk": True,
            })
        except Exception as exc:
            print(f"[MOBILE TELEOP STATUS ERROR] {exc}", flush=True)
