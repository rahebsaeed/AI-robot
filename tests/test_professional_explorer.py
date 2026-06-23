import tempfile
import unittest
from types import SimpleNamespace

import numpy as np

from core.search_tasks import is_search_request, is_searchable_target, parse_search_target
import tools.professional_explorer as explorer_module
from tools.professional_explorer import ExplorationMemory, FrontierExplorer


class MemoryStub:
    def __init__(self):
        self.visited = []
        self.failed = []

    def visited_near(self, goal, radius):
        return any(
            np.hypot(item["x"] - goal["x"], item["y"] - goal["y"]) <= radius
            for item in self.visited
        )

    def failure_penalty(self, goal):
        return 0


class ProfessionalExplorerTest(unittest.TestCase):
    def make_explorer(self):
        explorer = FrontierExplorer.__new__(FrontierExplorer)
        explorer.args = SimpleNamespace(
            free_threshold=20,
            occupied_threshold=65,
            minimum_clearance=0.10,
            minimum_frontier_length=0.30,
            gain_radius=1.5,
            frontier_goal_spacing=1.5,
            maximum_goals_per_frontier=8,
            visited_radius=0.85,
            maximum_failures=2,
            astar_max_expansions=20000,
            path_waypoint_spacing=0.30,
            plan_candidates=12,
            plan_tolerance=0.30,
            gain_weight=2.2,
            distance_weight=1.0,
            failure_weight=3.0,
            allow_near_goals=True,
            minimum_goal_distance=0.10,
            minimum_near_goal_distance=0.05,
        )
        explorer.memory = MemoryStub()
        explorer.last_coverage = 0.0
        explorer.use_move_base = False
        explorer.make_plan = None
        return explorer

    def test_frontier_detection_splits_large_boundary_into_regions(self):
        grid = np.full((60, 60), -1, dtype=np.int16)
        grid[10:50, 10:50] = 0
        snapshot = {
            "grid": grid,
            "resolution": 0.10,
            "origin_x": 0.0,
            "origin_y": 0.0,
            "origin_yaw": 0.0,
        }
        explorer = self.make_explorer()

        candidates, coverage, frontier_cells = explorer.frontier_candidates(
            snapshot, {"x": 3.0, "y": 3.0}
        )

        self.assertGreater(frontier_cells, 100)
        self.assertGreater(len(candidates), 1)
        self.assertLessEqual(len(candidates), 8)
        self.assertGreater(coverage, 40.0)

    def test_visited_region_is_not_selected_again(self):
        grid = np.full((30, 30), -1, dtype=np.int16)
        grid[8:22, 8:22] = 0
        snapshot = {
            "grid": grid,
            "resolution": 0.10,
            "origin_x": 0.0,
            "origin_y": 0.0,
            "origin_yaw": 0.0,
        }
        explorer = self.make_explorer()
        first, _, _ = explorer.frontier_candidates(snapshot, {"x": 1.5, "y": 1.5})
        explorer.memory.visited = [first[0]]

        second, _, _ = explorer.frontier_candidates(snapshot, {"x": 1.5, "y": 1.5})

        self.assertLess(len(second), len(first))

    def test_memory_is_written_atomically(self):
        with tempfile.TemporaryDirectory() as directory:
            path = directory + "/memory.json"
            memory = ExplorationMemory(path, "lab")
            memory.mark_visited({"x": 1.0, "y": 2.0, "gain": 3.0})
            self.assertEqual(1, len(memory.data["visited_goals"]))
            with open(path, "r") as handle:
                self.assertIn('"map_name": "lab"', handle.read())

    def test_completion_record_contains_verifiable_evidence(self):
        with tempfile.TemporaryDirectory() as directory:
            memory = ExplorationMemory(directory + "/memory.json", "lab")
            memory.mark_visited({"x": 1.0, "y": 2.0, "gain": 3.0})
            memory.mark_completed(1.25)

            self.assertEqual(2, memory.data["completion_version"])
            self.assertEqual(1, memory.data["completed_goal_count"])
            self.assertEqual(1.25, memory.data["coverage"])

    def test_weak_exploration_cannot_be_marked_complete(self):
        explorer = self.make_explorer()
        explorer.args.minimum_completion_coverage = 0.75
        explorer.args.minimum_completion_goals = 2
        explorer.memory.data = {"visited_goals": [{"x": 0.1, "y": 0.1}]}

        self.assertFalse(explorer.has_completion_evidence(0.26))
        explorer.memory.data["visited_goals"].append({"x": 1.0, "y": 1.0})
        self.assertTrue(explorer.has_completion_evidence(0.80))

    def test_geometric_plan_fallback_needs_no_ros_service(self):
        explorer = self.make_explorer()
        grid = np.zeros((30, 30), dtype=np.int16)
        grid[:, 15] = 100
        grid[14:17, 15] = 0
        snapshot = {
            "grid": grid,
            "resolution": 0.10,
            "origin_x": 0.0,
            "origin_y": 0.0,
            "origin_yaw": 0.0,
        }
        candidate = {
            "x": 2.5,
            "y": 1.5,
            "row": 15,
            "column": 25,
            "straight_distance": 2.0,
        }
        length = explorer.plan_to(
            snapshot,
            {"x": 0.5, "y": 1.5, "yaw": 0.0},
            candidate,
        )
        self.assertIsNotNone(length)
        self.assertGreater(length, 1.5)
        self.assertTrue(candidate["direct_path"])

    def test_search_target_cleanup_drops_spurious_leading_letters(self):
        self.assertEqual("door", parse_search_target("where is located the d door"))
        self.assertEqual("bottle", parse_search_target("search for b bottle"))

    def test_robot_pose_question_is_not_object_search(self):
        self.assertFalse(is_search_request("where are you"))
        self.assertFalse(is_searchable_target("you"))
        self.assertTrue(is_search_request("where is the door"))

    def test_close_startup_frontier_can_be_selected(self):
        explorer_module.rospy = SimpleNamespace(logwarn=lambda *args, **kwargs: None)
        explorer = self.make_explorer()
        explorer.args.minimum_goal_distance = 0.75
        explorer.args.minimum_near_goal_distance = 0.05
        explorer.args.allow_near_goals = True
        explorer.plan_to = lambda _snapshot, _robot, candidate: candidate["straight_distance"]
        candidate = {
            "x": 0.18,
            "y": 0.0,
            "row": 1,
            "column": 1,
            "frontier_cells": 118,
            "gain": 0.4,
            "straight_distance": 0.18,
            "failure_count": 0,
        }

        goal = explorer.choose_goal({}, [candidate], {"x": 0.0, "y": 0.0, "yaw": 0.0})

        self.assertIsNotNone(goal)
        self.assertTrue(goal["near_goal"])
        self.assertEqual(0.18, goal["path_length"])

    def test_move_base_plan_is_used_before_internal_astar(self):
        explorer = self.make_explorer()
        explorer.use_move_base = True
        explorer.pose_stamped = lambda x, y, yaw: SimpleNamespace(x=x, y=y, yaw=yaw)
        explorer.astar_plan = lambda *_args, **_kwargs: self.fail("A* should not run when move_base plan is valid")
        explorer_module.GetPlanRequest = lambda: SimpleNamespace(start=None, goal=None, tolerance=0.0)

        def pose(x, y):
            return SimpleNamespace(pose=SimpleNamespace(position=SimpleNamespace(x=x, y=y)))

        explorer.make_plan = lambda _request: SimpleNamespace(
            plan=SimpleNamespace(poses=[pose(0.0, 0.0), pose(0.3, 0.4)])
        )
        candidate = {"x": 0.3, "y": 0.4}

        length = explorer.plan_to({}, {"x": 0.0, "y": 0.0, "yaw": 0.0}, candidate)

        self.assertAlmostEqual(0.5, length)
        self.assertEqual(0.5, candidate["service_path_length"])


if __name__ == "__main__":
    unittest.main()
