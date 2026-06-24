import tempfile
import sys
import types
import unittest

torch_stub = types.ModuleType("torch")
torch_stub.cuda = types.SimpleNamespace(
    is_available=lambda: False,
    empty_cache=lambda: None,
    get_device_name=lambda _index: "test-gpu",
)
torch_stub.bfloat16 = object()
torch_stub.float32 = object()
torch_stub.no_grad = lambda: types.SimpleNamespace(
    __enter__=lambda self: None,
    __exit__=lambda self, exc_type, exc, tb: None,
)
sys.modules.setdefault("torch", torch_stub)

transformers_stub = types.ModuleType("transformers")
transformers_stub.AutoModelForCausalLM = object
transformers_stub.AutoTokenizer = object
sys.modules.setdefault("transformers", transformers_stub)

sr_stub = types.ModuleType("speech_recognition")
sr_stub.Microphone = type("Microphone", (), {"list_microphone_names": staticmethod(lambda: [])})
sr_stub.Recognizer = object
sys.modules.setdefault("speech_recognition", sr_stub)

whisper_stub = types.ModuleType("whisper")
sys.modules.setdefault("whisper", whisper_stub)

ultralytics_stub = types.ModuleType("ultralytics")
ultralytics_stub.YOLO = object
sys.modules.setdefault("ultralytics", ultralytics_stub)

from core.brain import Brain
from core.places_memory import PlacesMemory
from main import (
    build_place_navigation_goals,
    build_safe_action,
    describe_current_place,
    describe_map_identity,
    is_map_name_request,
)


class FakeRobot:
    def __init__(self, x=-1.19, y=5.70, yaw=1.85):
        self.pose = {"x": x, "y": y, "yaw": yaw, "age": 25.0}

    def get_mobile_state(self):
        return {
            "localized": True,
            "x": self.pose["x"],
            "y": self.pose["y"],
            "yaw": self.pose["yaw"],
            "pose_age": self.pose["age"],
        }

    def get_amcl_pose(self):
        return self.pose


class FakeApproachRobot(FakeRobot):
    def get_waypoints_near(self, map_name, x, y, yaw=0.0, clearance_m=0.45, max_points=12):
        return [
            {"x": x, "y": y, "yaw": yaw, "kind": "saved"},
            {"x": x + 0.7, "y": y, "yaw": 3.14, "kind": "approach", "radius": 0.7},
            {"x": x, "y": y + 0.7, "yaw": -1.57, "kind": "approach", "radius": 0.7},
        ]


class AiResponseLogicTest(unittest.TestCase):
    def make_places(self):
        tmp = tempfile.NamedTemporaryFile(delete=False)
        tmp.write(b"{}")
        tmp.close()
        memory = PlacesMemory(tmp.name)
        memory.places = {}
        memory.save_place("salle_robotique", "initial position", -1.26, 5.79, 1.30)
        memory.save_place("salle_robotique", "door", -4.0, 2.0, 0.0)
        return memory

    def test_plain_text_brain_reply_becomes_speech(self):
        result = Brain._plain_text_fallback("The current map is named salle_robotique.")

        self.assertEqual("The current map is named salle_robotique.", result["speech"])
        self.assertEqual("stop", result["action"]["move"])

    def test_map_name_question_is_deterministic(self):
        memory = self.make_places()

        self.assertTrue(is_map_name_request("what's the name of this map"))
        self.assertTrue(is_map_name_request("which this map"))
        self.assertIn("salle_robotique", describe_map_identity("salle_robotique", memory))

    def test_current_place_uses_nearest_saved_place(self):
        speech = describe_current_place(
            FakeRobot(),
            "salle_robotique",
            self.make_places(),
        )

        self.assertIn("initial position", speech)
        self.assertIn("salle_robotique", speech)

    def test_unrequested_arm_action_is_suppressed_without_losing_speech(self):
        result = build_safe_action(
            command="please define your place",
            model_data={
                "speech": "My current location is at the initial position.",
                "action": {"move": "stop", "speed": 0.0, "arm": "pickup"},
            },
            vision_data={"objects": [], "description": "Camera active."},
            lidar_distance=1.5,
        )

        self.assertEqual("My current location is at the initial position.", result["speech"])
        self.assertEqual("home", result["action"]["arm"])

    def test_saved_place_navigation_builds_alternate_approach_goals(self):
        goals = build_place_navigation_goals(
            FakeApproachRobot(),
            "salle_robotique",
            {"x": -4.0, "y": 2.0, "yaw": 0.0},
            max_goals=4,
        )

        self.assertEqual("saved", goals[0]["kind"])
        self.assertGreaterEqual(len(goals), 3)
        self.assertEqual(1, sum(1 for goal in goals if goal["kind"] == "saved"))
        self.assertTrue(any(goal["kind"] == "approach" for goal in goals))


if __name__ == "__main__":
    unittest.main()
