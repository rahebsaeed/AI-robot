#!/usr/bin/env python3
"""Standalone WebSocket robot simulator for testing the Android application."""

import argparse
import asyncio
import base64
import io
import json
import math
import signal
import socket
import time
import uuid
from typing import Dict, Optional, Set

from PIL import Image, ImageDraw


class MockRobotServer:
    def __init__(self, host: str, port: int, token: str):
        self.host = host
        self.port = port
        self.token = token
        self.clients: Set = set()
        self.shutdown_event = asyncio.Event()
        self.pose = {"x": -4.5, "y": -2.5, "yaw": 0.0}
        self.navigation_goal = None
        self.teleop_direction = "stop"
        self.teleop_speed = 0.0
        self.teleop_deadline = 0.0
        self.active_search_task: Optional[asyncio.Task] = None
        self.active_search_request = ""
        self.active_target = ""
        self.map_payload = self._make_map()

    async def run(self):
        import websockets

        print("=" * 64)
        print(" Robot Companion mock server")
        print(f" WebSocket: ws://{self.host}:{self.port}")
        print(f" Pairing token: {self.token or '(empty)'}")
        print(" PC addresses:")
        for address in self._local_addresses():
            print(f"   ws://{address}:{self.port}")
        print("=" * 64, flush=True)

        async with websockets.serve(
            self._handle_client,
            self.host,
            self.port,
            ping_interval=20,
            ping_timeout=10,
            max_size=2 * 1024 * 1024,
        ):
            telemetry = asyncio.create_task(self._telemetry_loop())
            await self.shutdown_event.wait()
            telemetry.cancel()
            if self.active_search_task:
                self.active_search_task.cancel()
            tasks = [telemetry]
            if self.active_search_task is not None:
                tasks.append(self.active_search_task)
            await asyncio.gather(*tasks, return_exceptions=True)

    def stop(self):
        self.shutdown_event.set()

    async def _handle_client(self, websocket, path=None):
        client_id = "unknown"
        authenticated = False
        try:
            raw = await asyncio.wait_for(websocket.recv(), timeout=6.0)
            hello = self._decode(raw)
            if hello.get("type") != "hello":
                await self._error(websocket, "HELLO_REQUIRED", "First message must be hello.")
                await websocket.close(code=4001, reason="hello required")
                return
            if self.token and str(hello.get("token", "")) != self.token:
                await self._error(websocket, "AUTH_FAILED", "Invalid pairing token.")
                await websocket.close(code=4003, reason="authentication failed")
                return

            client_id = str(hello.get("client_id") or uuid.uuid4())
            authenticated = True
            self.clients.add(websocket)
            print(f"[MOCK] connected: {client_id}", flush=True)
            await self._send(websocket, {
                "v": 1,
                "type": "hello_ack",
                "session_id": str(uuid.uuid4()),
                "client_id": client_id,
                "robot": "Rosmaster X3 Plus Simulator",
                "server_time": time.time(),
                "capabilities": [
                    "command", "stop", "search_cancel", "teleop",
                    "map", "robot_pose", "navigation_goal", "status",
                ],
            })
            await self._send(websocket, self._status(
                "ready", "listening", "Simulator ready."
            ))
            await self._send(websocket, self.map_payload)
            await self._send(websocket, self._pose_payload())

            async for raw in websocket:
                try:
                    await self._handle_message(websocket, self._decode(raw), client_id)
                except (ValueError, json.JSONDecodeError) as exc:
                    await self._error(websocket, "INVALID_MESSAGE", str(exc))
        except asyncio.TimeoutError:
            await websocket.close(code=4001, reason="hello timeout")
        except Exception as exc:
            if authenticated:
                print(f"[MOCK] client error: {exc}", flush=True)
        finally:
            self.clients.discard(websocket)
            if authenticated:
                self._stop_manual_motion()
                print(f"[MOCK] disconnected: {client_id}", flush=True)

    async def _handle_message(self, websocket, message: Dict, client_id: str):
        message_type = str(message.get("type", "")).lower()
        request_id = str(message.get("request_id") or uuid.uuid4())

        if message_type == "ping":
            await self._send(websocket, {
                "v": 1,
                "type": "pong",
                "request_id": request_id,
                "client_time": message.get("ts"),
                "server_time": time.time(),
            })
            return

        if message_type in {"map_request", "get_map"}:
            await self._send(websocket, self.map_payload)
            return

        if message_type == "command":
            text = str(message.get("text", "")).strip()
            if not text:
                await self._error(websocket, "INVALID_COMMAND", "Command is empty.", request_id)
                return
            await self._ack(websocket, request_id, "queued")
            await self._broadcast({
                "v": 1,
                "type": "processing",
                "request_id": request_id,
                "text": text,
                "source": "android",
                "timestamp": time.time(),
            })
            print(
                "[MOCK COMMAND] "
                f"request={request_id} client={client_id} "
                f"source={message.get('source', 'unknown')} "
                f"locale={message.get('locale', 'unknown')} text={text!r}",
                flush=True,
            )
            if self._is_search_command(text):
                await self._start_search(request_id, self._search_target(text))
            else:
                asyncio.create_task(self._answer_command(request_id, text))
            return

        if message_type == "stop":
            await self._ack(websocket, request_id, "executing")
            await self._cancel_active_search("stopped")
            self._stop_manual_motion()
            self.navigation_goal = None
            await self._broadcast(self._status(
                "stopped", "stopped", "All simulated movement stopped."
            ))
            await self._response(request_id, "All robot operations are stopped.", "stopped")
            return

        if message_type == "search_cancel":
            await self._ack(websocket, request_id, "executing")
            await self._cancel_active_search("cancelled")
            self.navigation_goal = None
            await self._broadcast(self._status(
                "stopped", "cancelled", "The simulated search was cancelled."
            ))
            await self._response(request_id, "The current search is cancelled.", "cancelled")
            return

        if message_type == "teleop":
            direction = str(message.get("direction", "stop")).lower()
            active = bool(message.get("active", True))
            try:
                speed = max(0.0, min(1.0, float(message.get("speed", 0.0))))
            except (TypeError, ValueError):
                speed = 0.0
            if direction not in {"forward", "backward", "left", "right", "stop"}:
                await self._error(websocket, "INVALID_TELEOP", "Invalid direction.", request_id)
                return
            if not active or direction == "stop" or speed <= 0.0:
                self._stop_manual_motion()
            else:
                if self.active_search_task:
                    await self._cancel_active_search("manual control")
                self.navigation_goal = None
                self.teleop_direction = direction
                self.teleop_speed = speed
                self.teleop_deadline = time.monotonic() + 0.65
            await self._ack(websocket, request_id, "active" if active else "stopped")
            return

        if message_type == "ui_action" and message.get("action") == "show_rviz":
            await self._ack(websocket, request_id, "executing")
            await self._response(request_id, "RViz is simulated by the Android map view.")
            return

        await self._error(
            websocket, "UNKNOWN_TYPE", f"Unsupported message type: {message_type}", request_id
        )

    async def _answer_command(self, request_id: str, text: str):
        await asyncio.sleep(0.65)
        lowered = text.lower()
        if any(word in lowered for word in ("hello", "hi", "good morning")):
            answer = "Hello. The PC robot simulator is connected and ready."
        elif "where are you" in lowered or "position" in lowered:
            answer = f"My simulated position is x {self.pose['x']:.2f}, y {self.pose['y']:.2f}."
        elif "what do you see" in lowered or "camera" in lowered:
            answer = "The simulated camera detects a bottle and a chair."
        elif any(word in lowered for word in ("forward", "backward", "left", "right")):
            answer = "Use the manual direction controls to test continuous simulated movement."
        else:
            answer = f"The simulator received your command: {text}"
        await self._broadcast(self._status("ready", "listening", "Simulator ready."))
        await self._response(request_id, answer)

    async def _start_search(self, request_id: str, target: str):
        await self._cancel_active_search("replaced")
        self.active_search_request = request_id
        self.active_target = target
        self.active_search_task = asyncio.create_task(self._simulate_search(request_id, target))

    async def _simulate_search(self, request_id: str, target: str):
        waypoints = [
            (-3.5, -1.5),
            (-1.5, -1.0),
            (0.5, 0.5),
            (2.5, 1.5),
            (4.0, 2.5),
        ]
        total = len(waypoints)
        try:
            await self._broadcast(self._status(
                "searching", "starting", f"Starting simulated search for {target}.",
                target=target, waypoint_index=0, waypoint_total=total,
            ))
            for index, (goal_x, goal_y) in enumerate(waypoints, start=1):
                self.navigation_goal = {"x": goal_x, "y": goal_y, "yaw": 0.0, "frame": "map"}
                current_waypoint = {"x": goal_x, "y": goal_y, "yaw": 0.0}
                await self._broadcast(self._status(
                    "searching", "navigating",
                    f"Moving to simulated search point {index}/{total}",
                    target=target, waypoint_index=index, waypoint_total=total,
                    searched_count=index - 1, current_waypoint=current_waypoint,
                ))
                await self._animate_to(goal_x, goal_y, duration=1.4)
                await self._broadcast(self._status(
                    "searching", "checking_camera",
                    f"Checking the camera for {target}",
                    target=target, waypoint_index=index, waypoint_total=total,
                    searched_count=index, current_waypoint=current_waypoint,
                ))
                await asyncio.sleep(0.55)
                if index == 4:
                    self.navigation_goal = None
                    await self._broadcast(self._status(
                        "found", "found", f"Simulated {target} detected.",
                        target=target, waypoint_index=index, waypoint_total=total,
                        searched_count=index, found=True,
                    ))
                    await self._response(
                        request_id,
                        f"I found the {target} in the simulated map.",
                        "completed",
                        found=True,
                        where=f"x={self.pose['x']:.2f} y={self.pose['y']:.2f}",
                    )
                    return
        except asyncio.CancelledError:
            raise
        finally:
            if self.active_search_request == request_id:
                self.active_search_request = ""
                self.active_target = ""
                self.active_search_task = None

    async def _animate_to(self, goal_x: float, goal_y: float, duration: float):
        start_x, start_y = self.pose["x"], self.pose["y"]
        dx, dy = goal_x - start_x, goal_y - start_y
        self.pose["yaw"] = math.atan2(dy, dx)
        steps = max(1, int(duration / 0.1))
        for step in range(1, steps + 1):
            ratio = step / steps
            self.pose["x"] = start_x + dx * ratio
            self.pose["y"] = start_y + dy * ratio
            await asyncio.sleep(duration / steps)

    async def _cancel_active_search(self, reason: str):
        task = self.active_search_task
        original_request = self.active_search_request
        target = self.active_target
        if task and not task.done():
            task.cancel()
            await asyncio.gather(task, return_exceptions=True)
            if original_request and reason != "replaced":
                await self._response(
                    original_request,
                    f"Search for {target or 'the object'} was cancelled.",
                    "cancelled",
                )
        self.active_search_task = None
        self.active_search_request = ""
        self.active_target = ""

    async def _telemetry_loop(self):
        last = time.monotonic()
        while True:
            await asyncio.sleep(0.1)
            now = time.monotonic()
            dt = min(0.2, now - last)
            last = now
            if self.teleop_direction != "stop":
                if now >= self.teleop_deadline:
                    self._stop_manual_motion()
                    await self._broadcast(self._status(
                        "ready", "manual_stopped", "Manual control heartbeat timed out."
                    ))
                else:
                    linear = 1.2 * self.teleop_speed
                    angular = 2.2 * self.teleop_speed
                    if self.teleop_direction == "forward":
                        self.pose["x"] += math.cos(self.pose["yaw"]) * linear * dt
                        self.pose["y"] += math.sin(self.pose["yaw"]) * linear * dt
                    elif self.teleop_direction == "backward":
                        self.pose["x"] -= math.cos(self.pose["yaw"]) * linear * dt
                        self.pose["y"] -= math.sin(self.pose["yaw"]) * linear * dt
                    elif self.teleop_direction == "left":
                        self.pose["yaw"] += angular * dt
                    elif self.teleop_direction == "right":
                        self.pose["yaw"] -= angular * dt
                    self.pose["yaw"] = math.atan2(
                        math.sin(self.pose["yaw"]), math.cos(self.pose["yaw"])
                    )
            await self._broadcast(self._pose_payload())

    def _stop_manual_motion(self):
        self.teleop_direction = "stop"
        self.teleop_speed = 0.0
        self.teleop_deadline = 0.0

    def _pose_payload(self):
        return {
            "v": 1,
            "type": "robot_pose",
            "timestamp": time.time(),
            "localized": True,
            "frame": "map",
            "x": round(self.pose["x"], 4),
            "y": round(self.pose["y"], 4),
            "yaw": round(self.pose["yaw"], 4),
            "pose_age": 0.02,
            "navigation_goal": self.navigation_goal,
        }

    @staticmethod
    def _status(mode: str, phase: str, message: str, **extra):
        payload = {
            "v": 1,
            "type": "status",
            "mode": mode,
            "phase": phase,
            "message": message,
            "target": "",
            "waypoint_index": 0,
            "waypoint_total": 0,
            "searched_count": 0,
            "found": False,
            "current_waypoint": None,
            "can_talk": True,
        }
        payload.update(extra)
        return payload

    async def _response(self, request_id: str, text: str,
                        status: str = "completed", **extra):
        payload = {
            "v": 1,
            "type": "response",
            "request_id": request_id,
            "text": text,
            "source": "android",
            "status": status,
            "timestamp": time.time(),
        }
        payload.update(extra)
        await self._broadcast(payload)

    async def _ack(self, websocket, request_id: str, state: str):
        await self._send(websocket, {
            "v": 1,
            "type": "ack",
            "request_id": request_id,
            "state": state,
            "timestamp": time.time(),
        })

    async def _error(self, websocket, code: str, message: str,
                     request_id: str = ""):
        await self._send(websocket, {
            "v": 1,
            "type": "error",
            "request_id": request_id,
            "code": code,
            "message": message,
            "timestamp": time.time(),
        })

    async def _broadcast(self, payload: Dict):
        if not self.clients:
            return
        encoded = json.dumps(payload, separators=(",", ":"))
        clients = list(self.clients)
        results = await asyncio.gather(
            *(client.send(encoded) for client in clients), return_exceptions=True
        )
        for client, result in zip(clients, results):
            if isinstance(result, Exception):
                self.clients.discard(client)

    @staticmethod
    async def _send(websocket, payload: Dict):
        await websocket.send(json.dumps(payload, separators=(",", ":")))

    @staticmethod
    def _decode(raw) -> Dict:
        if isinstance(raw, bytes):
            raw = raw.decode("utf-8")
        value = json.loads(raw)
        if not isinstance(value, dict):
            raise ValueError("Message must be a JSON object")
        return value

    @staticmethod
    def _is_search_command(text: str) -> bool:
        lowered = text.lower()
        return any(part in lowered for part in (
            "search for", "look for", "find the", "find a", "locate",
        ))

    @staticmethod
    def _search_target(text: str) -> str:
        lowered = text.lower()
        for phrase in ("search for", "look for", "find the", "find a", "locate"):
            if phrase in lowered:
                target = lowered.split(phrase, 1)[1].strip(" .?!")
                for article in ("the ", "a ", "an "):
                    if target.startswith(article):
                        target = target[len(article):]
                        break
                return target or "object"
        return "object"

    @staticmethod
    def _make_map():
        width, height = 640, 480
        image = Image.new("L", (width, height), 205)
        draw = ImageDraw.Draw(image)
        draw.rectangle((24, 24, width - 25, height - 25), fill=254, outline=0, width=9)
        draw.line((250, 24, 250, 205), fill=0, width=8)
        draw.line((250, 270, 250, height - 24), fill=0, width=8)
        draw.line((405, 145, width - 24, 145), fill=0, width=8)
        draw.rectangle((85, 95, 145, 155), fill=0)
        draw.rectangle((445, 300, 535, 350), fill=0)
        for x in range(40, width - 40, 40):
            draw.line((x, 30, x, height - 30), fill=238, width=1)
        for y in range(40, height - 40, 40):
            draw.line((30, y, width - 30, y), fill=238, width=1)
        output = io.BytesIO()
        image.save(output, format="PNG", optimize=True)
        encoded = base64.b64encode(output.getvalue()).decode("ascii")
        return {
            "v": 1,
            "type": "map",
            "map_id": "pc-simulator-v1",
            "name": "simulated_laboratory",
            "encoding": "png_base64",
            "image_base64": encoded,
            "width": width,
            "height": height,
            "resolution": 0.025,
            "origin_x": -8.0,
            "origin_y": -6.0,
            "origin_yaw": 0.0,
            "frame": "map",
        }

    @staticmethod
    def _local_addresses():
        addresses = set()
        try:
            for info in socket.getaddrinfo(socket.gethostname(), None, socket.AF_INET):
                address = info[4][0]
                if not address.startswith("127."):
                    addresses.add(address)
        except OSError:
            pass
        try:
            probe = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
            probe.connect(("8.8.8.8", 80))
            addresses.add(probe.getsockname()[0])
            probe.close()
        except OSError:
            pass
        return sorted(addresses) or ["YOUR_PC_IP"]


def parse_args():
    parser = argparse.ArgumentParser(
        description="Simulate the robot WebSocket API for the Android app."
    )
    parser.add_argument("--host", default="0.0.0.0")
    parser.add_argument("--port", type=int, default=8765)
    parser.add_argument("--token", default="test-token")
    return parser.parse_args()


async def async_main():
    args = parse_args()
    server = MockRobotServer(args.host, args.port, args.token)
    loop = asyncio.get_running_loop()
    for signal_name in (signal.SIGINT, signal.SIGTERM):
        try:
            loop.add_signal_handler(signal_name, server.stop)
        except NotImplementedError:
            pass
    await server.run()


if __name__ == "__main__":
    try:
        asyncio.run(async_main())
    except KeyboardInterrupt:
        pass
