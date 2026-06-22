import queue
import re
import threading
import time
import uuid
from dataclasses import dataclass
from typing import Optional


_STOP_PATTERN = re.compile(
    r"^\s*(?:please\s+)?(?:stop|halt|freeze|emergency\s+stop|stop\s+now|stop\s+(?:all|moving|the\s+robot|everything))[.!?\s]*$",
    re.IGNORECASE,
)
_SEARCH_CANCEL_PATTERN = re.compile(
    r"^\s*(?:please\s+)?(?:stop|cancel)(?:\s+(?:the\s+)?current|\s+the)?\s+search(?:ing)?[.!?\s]*$",
    re.IGNORECASE,
)


def is_emergency_stop_text(text: str) -> bool:
    return bool(_STOP_PATTERN.match(str(text or "")))


def is_search_cancel_text(text: str) -> bool:
    return bool(_SEARCH_CANCEL_PATTERN.match(str(text or "")))


@dataclass(frozen=True)
class CommandEnvelope:
    request_id: str
    text: str
    source: str
    locale: str
    received_at: float
    priority: int


class CommandBus:
    """Single bounded command input for the robot microphone and mobile clients."""

    def __init__(self, max_size: int = 32):
        self._queue = queue.PriorityQueue(maxsize=max(1, int(max_size)))
        self._sequence = 0
        self._lock = threading.Lock()

    @staticmethod
    def _priority_for(text: str) -> int:
        lowered = str(text).lower()
        if is_emergency_stop_text(text):
            return 0
        if any(word in lowered for word in ("cancel", "stop searching", "stop search")):
            return 10
        if any(word in lowered for word in ("move", "forward", "backward", "left", "right")):
            return 20
        return 50

    def submit(
        self,
        text: str,
        source: str,
        request_id: Optional[str] = None,
        locale: str = "en-US",
    ) -> Optional[CommandEnvelope]:
        text = re.sub(r"\s+", " ", str(text or "")).strip()
        if not text or len(text) > 500:
            return None

        envelope = CommandEnvelope(
            request_id=str(request_id or uuid.uuid4()),
            text=text,
            source=str(source or "unknown"),
            locale=str(locale or "en-US"),
            received_at=time.time(),
            priority=self._priority_for(text),
        )
        with self._lock:
            self._sequence += 1
            sequence = self._sequence
        try:
            self._queue.put_nowait((envelope.priority, sequence, envelope))
        except queue.Full:
            return None
        return envelope

    def get(self, timeout: float = 0.25) -> Optional[CommandEnvelope]:
        try:
            _, _, envelope = self._queue.get(timeout=max(0.01, float(timeout)))
            return envelope
        except queue.Empty:
            return None

    def pending_count(self) -> int:
        return self._queue.qsize()


class MicrophoneCommandProducer:
    """Keeps the physical robot microphone available alongside Android input."""

    def __init__(self, perceptions, command_bus: CommandBus,
                 emergency_callback=None, search_cancel_callback=None):
        self.perceptions = perceptions
        self.command_bus = command_bus
        self.emergency_callback = emergency_callback
        self.search_cancel_callback = search_cancel_callback
        self._stop_event = threading.Event()
        self._thread = threading.Thread(target=self._run, name="robot-microphone", daemon=True)

    def start(self):
        self._thread.start()

    def stop(self):
        self._stop_event.set()

    def _run(self):
        print("[COMMAND BUS] Robot microphone producer started", flush=True)
        while not self._stop_event.is_set():
            try:
                text = self.perceptions.listen(timeout=1.5, phrase_time_limit=8)
            except Exception as exc:
                print(f"[COMMAND BUS MIC ERROR] {exc}", flush=True)
                self._stop_event.wait(0.5)
                continue

            if not text:
                continue
            if is_emergency_stop_text(text) and self.emergency_callback is not None:
                request_id = str(uuid.uuid4())
                self.emergency_callback("all", request_id, "robot_mic")
                continue
            if is_search_cancel_text(text) and self.search_cancel_callback is not None:
                request_id = str(uuid.uuid4())
                self.search_cancel_callback(request_id, "robot_mic")
                continue

            envelope = self.command_bus.submit(text, source="robot_mic", locale="en-US")
            if envelope is None:
                print("[COMMAND BUS] Microphone command rejected: queue full or invalid", flush=True)
            else:
                print(f"[COMMAND BUS] queued microphone request={envelope.request_id}", flush=True)
