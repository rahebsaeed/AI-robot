import json
import socket


class FaceBridge:
    """
    Sends messages and emotions to robot_face.py through UDP.

    robot_face.py listens on:
        127.0.0.1:5005
    """

    def __init__(self, host="127.0.0.1", port=5005):
        self.host = host
        self.port = port

    def _send(self, payload):
        try:
            sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
            sock.sendto(payload.encode("utf-8"), (self.host, self.port))
            sock.close()
        except Exception as e:
            print(f"[FaceBridge Error]: {e}")

    def send_message(self, text):
        if not text:
            return

        # Send full message.
        # robot_face.py will split it into 30-word chunks.
        self._send(f"msg:{text}")

    def send_status(self, status):
        if not isinstance(status, dict):
            return

        try:
            payload = json.dumps(status, ensure_ascii=False)
        except Exception:
            return
        self._send(f"status:{payload}")

    def send_emotion(self, emotion):
        if not emotion:
            return

        self._send(emotion)

    def show_ai_response(self, text):
        if not text:
            return

        emotion = self.detect_emotion(text)
        self.send_emotion(emotion)
        self.send_message(text)

    def detect_emotion(self, text):
        text = text.lower()

        if any(word in text for word in ["danger", "obstacle", "stop", "careful", "warning"]):
            return "fear"

        if any(word in text for word in ["hello", "hi", "good", "nice", "success", "ready"]):
            return "happy"

        if any(word in text for word in ["sorry", "error", "failed", "problem", "cannot"]):
            return "sad"

        if any(word in text for word in ["thinking", "search", "question", "calculate", "where", "how"]):
            return "thinking"

        return "neutral"