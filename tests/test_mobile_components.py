import time
import threading
import unittest

from core.command_bus import CommandBus, is_emergency_stop_text, is_search_cancel_text
from core.face_bridge import FaceBridge
from core.mobile_control import MobileTeleopController
from core.robot import Robot


class FakeRobot:
    def __init__(self):
        self.sent = []
        self.stop_count = 0
        self.cancel_count = 0

    def send_manual_velocity(self, direction, speed):
        self.sent.append((direction, speed))

    def stop(self):
        self.stop_count += 1

    def cancel_navigation(self):
        self.cancel_count += 1


class FakeSearch:
    def __init__(self):
        self.cancelled = False

    def request_cancel(self):
        self.cancelled = True


class MobileComponentsTest(unittest.TestCase):
    def test_command_bus_preserves_source_and_priority(self):
        bus = CommandBus(max_size=3)
        normal = bus.submit("find the bottle", source="android", request_id="normal")
        stop = bus.submit("stop", source="robot_mic", request_id="stop")
        self.assertIsNotNone(normal)
        self.assertIsNotNone(stop)
        self.assertEqual("stop", bus.get().request_id)
        self.assertEqual("normal", bus.get().request_id)

    def test_emergency_text_is_deliberately_narrow(self):
        self.assertTrue(is_emergency_stop_text("Please stop now"))
        self.assertFalse(is_emergency_stop_text("stop searching for the bottle"))
        self.assertTrue(is_search_cancel_text("cancel the current search"))

    def test_face_bridge_fans_out_status(self):
        events = []
        bridge = FaceBridge(port=59999)
        bridge.add_listener(events.append)
        bridge.send_status({"mode": "ready", "phase": "listening"})
        self.assertEqual("status", events[0]["type"])
        self.assertEqual("ready", events[0]["mode"])

    def test_teleop_watchdog_stops_after_heartbeat_timeout(self):
        robot = FakeRobot()
        search = FakeSearch()
        controller = MobileTeleopController(robot, search, timeout=0.35)
        try:
            self.assertTrue(controller.update("forward", 0.25, True, "phone"))
            time.sleep(0.75)
            self.assertTrue(search.cancelled)
            self.assertGreaterEqual(robot.cancel_count, 1)
            self.assertTrue(robot.sent)
            self.assertGreaterEqual(robot.stop_count, 2)
        finally:
            controller.close()

    def test_teleop_sends_while_navigation_cancel_is_running(self):
        class SlowCancelRobot(FakeRobot):
            def cancel_navigation(self):
                time.sleep(0.5)
                super().cancel_navigation()

        robot = SlowCancelRobot()
        controller = MobileTeleopController(robot, FakeSearch(), timeout=0.65)
        try:
            self.assertTrue(controller.update("left", 0.28, True, "phone"))
            time.sleep(0.3)
            self.assertTrue(robot.sent)
            self.assertEqual("left", robot.sent[0][0])
        finally:
            controller.close()

    def test_teleop_speed_uses_full_normalized_range(self):
        robot = FakeRobot()
        controller = MobileTeleopController(robot, FakeSearch(), timeout=0.65)
        try:
            self.assertTrue(controller.update("forward", 5.0, True, "phone"))
            time.sleep(0.25)
            self.assertTrue(robot.sent)
            self.assertEqual(1.0, robot.sent[0][1])
        finally:
            controller.close()

    def test_mobile_state_keeps_last_stationary_pose(self):
        robot = Robot.__new__(Robot)
        robot._pose_lock = threading.Lock()
        robot._cached_pose = {"x": 1.25, "y": -0.5, "yaw": 0.75}
        robot._pose_last_fetch = time.time() - 60.0
        robot._goal_lock = threading.Lock()
        robot._navigation_goal = None

        state = robot.get_mobile_state()

        self.assertTrue(state["localized"])
        self.assertEqual(1.25, state["x"])
        self.assertGreaterEqual(state["pose_age"], 60.0)


if __name__ == "__main__":
    unittest.main()
