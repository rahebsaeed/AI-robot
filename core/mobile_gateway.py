import asyncio
import hmac
import json
import os
import threading
import time
import uuid
from typing import Any, Callable, Dict, Optional

from core.command_bus import CommandBus, is_emergency_stop_text, is_search_cancel_text


class MobileGateway:
    PROTOCOL_VERSION = 1

    def __init__(
        self,
        command_bus: CommandBus,
        map_provider: Optional[Callable[[], Optional[Dict[str, Any]]]] = None,
        telemetry_provider: Optional[Callable[[], Dict[str, Any]]] = None,
        stop_callback=None,
        search_cancel_callback=None,
        teleop_callback=None,
        rviz_callback=None,
        mic_control_callback=None,
        mic_state_provider: Optional[Callable[[], bool]] = None,
        host: Optional[str] = None,
        port: Optional[int] = None,
        token: Optional[str] = None,
    ):
        self.command_bus = command_bus
        self.map_provider = map_provider
        self.telemetry_provider = telemetry_provider
        self.stop_callback = stop_callback
        self.search_cancel_callback = search_cancel_callback
        self.teleop_callback = teleop_callback
        self.rviz_callback = rviz_callback
        self.mic_control_callback = mic_control_callback
        self.mic_state_provider = mic_state_provider
        self.host = host or os.environ.get("AI_MOBILE_HOST", "0.0.0.0")
        self.port = int(port or os.environ.get("AI_MOBILE_PORT", "8765"))
        self.token = token if token is not None else os.environ.get("AI_MOBILE_TOKEN", "")
        self.enabled = os.environ.get("AI_MOBILE_ENABLED", "1") != "0"
        self._loop = None
        self._thread = None
        self._shutdown_event = None
        self._clients = set()
        self._last_status = {
            "v": self.PROTOCOL_VERSION,
            "type": "status",
            "mode": "starting",
            "phase": "starting",
            "message": "Robot system is starting.",
        }

    def start(self) -> bool:
        if not self.enabled:
            print("[MOBILE] WebSocket gateway disabled", flush=True)
            return False
        try:
            import websockets  # noqa: F401
        except ImportError:
            print("[MOBILE ERROR] Install dependency: pip3 install websockets", flush=True)
            return False

        self._thread = threading.Thread(target=self._run, name="mobile-websocket", daemon=True)
        self._thread.start()
        return True

    def stop(self):
        if self._loop is not None and self._shutdown_event is not None:
            self._loop.call_soon_threadsafe(self._shutdown_event.set)

    def _run(self):
        self._loop = asyncio.new_event_loop()
        asyncio.set_event_loop(self._loop)
        self._shutdown_event = asyncio.Event()
        try:
            self._loop.run_until_complete(self._serve())
        except Exception as exc:
            print(f"[MOBILE ERROR] WebSocket server stopped: {exc}", flush=True)
        finally:
            self._loop.close()
            self._loop = None

    async def _serve(self):
        import websockets

        auth_mode = "token" if self.token else "open development mode"
        print(f"[MOBILE] WebSocket ws://{self.host}:{self.port} ({auth_mode})", flush=True)
        if not self.token:
            print("[MOBILE WARNING] AI_MOBILE_TOKEN is empty; use only on a trusted LAN", flush=True)

        async with websockets.serve(
            self._handle_client,
            self.host,
            self.port,
            ping_interval=20,
            ping_timeout=10,
            max_size=2 * 1024 * 1024,
            max_queue=16,
        ):
            telemetry_task = asyncio.create_task(self._telemetry_loop())
            await self._shutdown_event.wait()
            telemetry_task.cancel()
            await asyncio.gather(telemetry_task, return_exceptions=True)

    async def _handle_client(self, websocket, path=None):
        client_id = "unknown"
        authenticated = False
        try:
            raw = await asyncio.wait_for(websocket.recv(), timeout=6.0)
            hello = self._decode_message(raw)
            if hello.get("type") != "hello":
                await self._send_error(websocket, "HELLO_REQUIRED", "First message must be hello.")
                await websocket.close(code=4001, reason="hello required")
                return

            supplied_token = str(hello.get("token", ""))
            if self.token and not hmac.compare_digest(supplied_token, self.token):
                await self._send_error(websocket, "AUTH_FAILED", "Invalid pairing token.")
                await websocket.close(code=4003, reason="authentication failed")
                return

            client_id = str(hello.get("client_id") or uuid.uuid4())[:80]
            authenticated = True
            self._clients.add(websocket)
            await self._send(websocket, {
                "v": self.PROTOCOL_VERSION,
                "type": "hello_ack",
                "session_id": str(uuid.uuid4()),
                "client_id": client_id,
                "robot": "Rosmaster X3 Plus",
                "server_time": time.time(),
                "capabilities": [
                    "command", "stop", "search_cancel", "teleop",
                    "map", "robot_pose", "navigation_goal", "status",
                    "robot_mic_control",
                ],
            })
            await self._send(websocket, self._last_status)
            await self._send_robot_mic_state(websocket)
            await self._send_map(websocket)

            async for raw in websocket:
                message = self._decode_message(raw)
                await self._handle_message(websocket, client_id, message)
        except asyncio.TimeoutError:
            try:
                await websocket.close(code=4001, reason="hello timeout")
            except Exception:
                pass
        except Exception as exc:
            if authenticated:
                print(f"[MOBILE] client {client_id} disconnected: {exc}", flush=True)
        finally:
            self._clients.discard(websocket)
            if authenticated and self.teleop_callback is not None:
                try:
                    self.teleop_callback("stop", 0.0, False, client_id)
                except Exception:
                    pass

    async def _handle_message(self, websocket, client_id: str, message: Dict[str, Any]):
        message_type = str(message.get("type", "")).lower()
        request_id = str(message.get("request_id") or uuid.uuid4())

        if message_type == "ping":
            await self._send(websocket, {
                "v": self.PROTOCOL_VERSION,
                "type": "pong",
                "request_id": request_id,
                "client_time": message.get("ts"),
                "server_time": time.time(),
            })
            return

        if message_type in {"map_request", "get_map"}:
            await self._send_map(websocket)
            return

        if message_type == "command":
            text = str(message.get("text", "")).strip()
            if not text or len(text) > 500:
                await self._send_error(websocket, "INVALID_COMMAND", "Command must contain 1 to 500 characters.", request_id)
                return
            if is_emergency_stop_text(text):
                await self._send_ack(websocket, request_id, "executing")
                if self.stop_callback:
                    self.stop_callback("all", request_id, "android")
                return
            if is_search_cancel_text(text):
                await self._send_ack(websocket, request_id, "executing")
                if self.search_cancel_callback:
                    self.search_cancel_callback(request_id, "android")
                return
            envelope = self.command_bus.submit(
                text,
                source="android",
                request_id=request_id,
                locale=str(message.get("locale", "en-US")),
            )
            if envelope is None:
                await self._send_error(websocket, "QUEUE_FULL", "Robot command queue is full or command is invalid.", request_id)
            else:
                await self._send_ack(websocket, request_id, "queued")
            return

        if message_type == "stop":
            await self._send_ack(websocket, request_id, "executing")
            if self.stop_callback:
                self.stop_callback(str(message.get("scope", "all")), request_id, "android")
            return

        if message_type == "search_cancel":
            await self._send_ack(websocket, request_id, "executing")
            if self.search_cancel_callback:
                self.search_cancel_callback(request_id, "android")
            return

        if message_type == "teleop":
            direction = str(message.get("direction", "stop")).lower()
            speed = message.get("speed", 0.0)
            active = bool(message.get("active", True))
            accepted = bool(
                self.teleop_callback
                and self.teleop_callback(direction, speed, active, client_id)
            )
            if not accepted:
                await self._send_error(websocket, "INVALID_TELEOP", "Invalid teleoperation command.", request_id)
            elif request_id:
                await self._send_ack(websocket, request_id, "active" if active else "stopped")
            return

        if message_type in {"robot_mic", "mic_control"}:
            enabled = bool(message.get("enabled", False))
            await self._send_ack(websocket, request_id, "executing")
            if self.mic_control_callback:
                self.mic_control_callback(enabled, request_id, "android")
            else:
                await self._send_error(websocket, "ROBOT_MIC_UNAVAILABLE", "Robot microphone control is not available.", request_id)
            return

        if message_type == "ui_action" and message.get("action") == "show_rviz":
            await self._send_ack(websocket, request_id, "executing")
            if self.rviz_callback:
                self.rviz_callback(request_id, "android")
            return

        await self._send_error(websocket, "UNKNOWN_TYPE", f"Unsupported message type: {message_type}", request_id)

    async def _send_map(self, websocket):
        if self.map_provider is None:
            return
        try:
            loop = asyncio.get_running_loop()
            snapshot = await loop.run_in_executor(None, self.map_provider)
            if snapshot:
                payload = {"v": self.PROTOCOL_VERSION, "type": "map"}
                payload.update(snapshot)
                await self._send(websocket, payload)
        except Exception as exc:
            await self._send_error(websocket, "MAP_UNAVAILABLE", str(exc))

    async def _telemetry_loop(self):
        last_payload = None
        last_sent = 0.0
        while True:
            await asyncio.sleep(0.20)
            if not self._clients or self.telemetry_provider is None:
                continue
            try:
                state = self.telemetry_provider() or {}
                if not state:
                    continue
                payload = {"v": self.PROTOCOL_VERSION, "type": "robot_pose", "timestamp": time.time()}
                payload.update(state)
                serialized = json.dumps(payload, sort_keys=True, separators=(",", ":"))
                now = time.monotonic()
                if serialized == last_payload and now - last_sent < 1.0:
                    continue
                last_payload = serialized
                last_sent = now
                await self._broadcast(payload)
            except asyncio.CancelledError:
                raise
            except Exception as exc:
                print(f"[MOBILE TELEMETRY ERROR] {exc}", flush=True)

    def broadcast_event(self, event: Dict[str, Any]):
        if not isinstance(event, dict):
            return
        payload = {"v": self.PROTOCOL_VERSION}
        payload.update(event)
        if payload.get("type") == "status":
            self._last_status = dict(payload)
        self._schedule_broadcast(payload)

    def publish_processing(self, request_id: str, text: str, source: str):
        self._schedule_broadcast({
            "v": self.PROTOCOL_VERSION,
            "type": "processing",
            "request_id": request_id,
            "text": text,
            "source": source,
            "timestamp": time.time(),
        })

    def publish_response(self, request_id: str, text: str, source: str, status="completed", **extra):
        payload = {
            "v": self.PROTOCOL_VERSION,
            "type": "response",
            "request_id": request_id,
            "text": str(text),
            "source": source,
            "status": status,
            "timestamp": time.time(),
        }
        payload.update(extra)
        self._schedule_broadcast(payload)

    def publish_robot_mic_state(self, enabled: bool, source: str = "robot"):
        self._schedule_broadcast({
            "v": self.PROTOCOL_VERSION,
            "type": "robot_mic",
            "enabled": bool(enabled),
            "source": source,
            "timestamp": time.time(),
        })

    async def _send_robot_mic_state(self, websocket):
        if self.mic_state_provider is None:
            return
        try:
            enabled = bool(self.mic_state_provider())
        except Exception:
            return
        await self._send(websocket, {
            "v": self.PROTOCOL_VERSION,
            "type": "robot_mic",
            "enabled": enabled,
            "source": "robot",
            "timestamp": time.time(),
        })

    def _schedule_broadcast(self, payload: Dict[str, Any]):
        if self._loop is None or not self._loop.is_running():
            return
        asyncio.run_coroutine_threadsafe(self._broadcast(payload), self._loop)

    async def _broadcast(self, payload: Dict[str, Any]):
        if not self._clients:
            return
        data = json.dumps(payload, ensure_ascii=False, separators=(",", ":"))
        clients = list(self._clients)
        results = await asyncio.gather(*(client.send(data) for client in clients), return_exceptions=True)
        for client, result in zip(clients, results):
            if isinstance(result, Exception):
                self._clients.discard(client)

    @staticmethod
    def _decode_message(raw) -> Dict[str, Any]:
        if isinstance(raw, bytes):
            raw = raw.decode("utf-8", errors="strict")
        value = json.loads(raw)
        if not isinstance(value, dict):
            raise ValueError("WebSocket message must be a JSON object")
        return value

    @staticmethod
    async def _send(websocket, payload: Dict[str, Any]):
        await websocket.send(json.dumps(payload, ensure_ascii=False, separators=(",", ":")))

    async def _send_ack(self, websocket, request_id: str, state: str):
        await self._send(websocket, {
            "v": self.PROTOCOL_VERSION,
            "type": "ack",
            "request_id": request_id,
            "state": state,
            "timestamp": time.time(),
        })

    async def _send_error(self, websocket, code: str, message: str, request_id: str = ""):
        await self._send(websocket, {
            "v": self.PROTOCOL_VERSION,
            "type": "error",
            "request_id": request_id,
            "code": code,
            "message": message,
            "timestamp": time.time(),
        })
