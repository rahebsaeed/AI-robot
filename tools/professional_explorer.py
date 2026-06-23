#!/usr/bin/env python
"""Persistent frontier exploration coordinator for ROS1 gmapping."""

from __future__ import print_function

import argparse
import heapq
import json
import math
import os
import threading
import time

import cv2
import numpy as np

ROS_IMPORT_ERROR = None
try:
    import actionlib
    import rospy
    import tf
    from actionlib_msgs.msg import GoalStatus
    from geometry_msgs.msg import PoseStamped, Twist
    from move_base_msgs.msg import MoveBaseAction, MoveBaseGoal
    from nav_msgs.msg import OccupancyGrid
    from nav_msgs.srv import GetPlan, GetPlanRequest
    from sensor_msgs.msg import LaserScan
    from std_srvs.srv import Empty
except ImportError as exc:
    ROS_IMPORT_ERROR = exc
    actionlib = None
    rospy = None
    tf = None


def distance(a, b):
    return math.hypot(float(a["x"]) - float(b["x"]), float(a["y"]) - float(b["y"]))


def is_finite(value):
    return not math.isnan(value) and not math.isinf(value)


def path_length(poses):
    total = 0.0
    previous = None
    for stamped in poses:
        point = stamped.pose.position
        current = (point.x, point.y)
        if previous is not None:
            total += math.hypot(current[0] - previous[0], current[1] - previous[1])
        previous = current
    return total


class ExplorationMemory(object):
    def __init__(self, path, map_name, reset=False):
        self.path = path
        self.map_name = map_name
        self.data = self._empty()
        if reset:
            try:
                os.remove(path)
            except OSError:
                pass
        self._load()

    def _empty(self):
        return {
            "version": 1,
            "map_name": self.map_name,
            "completed": False,
            "visited_goals": [],
            "failed_goals": [],
            "scanned_positions": [],
            "updated_at": time.time(),
        }

    def _load(self):
        if not os.path.isfile(self.path):
            return
        try:
            with open(self.path, "r") as handle:
                loaded = json.load(handle)
            if loaded.get("map_name") != self.map_name:
                rospy.logwarn("[MEMORY] Ignoring memory for another map")
                return
            for key in ("visited_goals", "failed_goals", "scanned_positions"):
                if not isinstance(loaded.get(key), list):
                    loaded[key] = []
            self.data = loaded
            rospy.loginfo(
                "[MEMORY] Loaded %d visited goals, %d failed goals and %d path samples",
                len(self.data["visited_goals"]),
                len(self.data["failed_goals"]),
                len(self.data["scanned_positions"]),
            )
        except Exception as exc:
            rospy.logwarn("[MEMORY] Could not load %s: %s", self.path, exc)
            self.data = self._empty()

    def save(self):
        directory = os.path.dirname(self.path)
        if directory and not os.path.isdir(directory):
            os.makedirs(directory)
        self.data["updated_at"] = time.time()
        temporary = self.path + ".tmp"
        with open(temporary, "w") as handle:
            json.dump(self.data, handle, indent=2, sort_keys=True)
            handle.write("\n")
        if os.path.exists(self.path):
            os.remove(self.path)
        os.rename(temporary, self.path)

    def record_position(self, pose, spacing):
        samples = self.data["scanned_positions"]
        if samples and distance(samples[-1], pose) < spacing:
            return False
        samples.append({"x": pose["x"], "y": pose["y"], "time": time.time()})
        self.data["scanned_positions"] = samples[-2000:]
        self.save()
        return True

    def mark_visited(self, goal):
        self.data["visited_goals"].append({
            "x": goal["x"],
            "y": goal["y"],
            "gain": goal.get("gain", 0.0),
            "near_goal": bool(goal.get("near_goal", False)),
            "time": time.time(),
        })
        self.data["visited_goals"] = self.data["visited_goals"][-500:]
        self.data["failed_goals"] = [
            failed for failed in self.data["failed_goals"]
            if distance(failed, goal) > 0.8
        ]
        self.save()

    def mark_failed(self, goal):
        for failed in self.data["failed_goals"]:
            if distance(failed, goal) <= 0.7:
                failed["attempts"] = int(failed.get("attempts", 1)) + 1
                failed["time"] = time.time()
                self.save()
                return
        self.data["failed_goals"].append({
            "x": goal["x"],
            "y": goal["y"],
            "attempts": 1,
            "time": time.time(),
        })
        self.data["failed_goals"] = self.data["failed_goals"][-300:]
        self.save()

    def visited_near(self, goal, radius):
        return any(distance(item, goal) <= radius for item in self.data["visited_goals"])

    def failure_penalty(self, goal):
        penalty = 0
        for item in self.data["failed_goals"]:
            if distance(item, goal) <= 1.0:
                penalty = max(penalty, int(item.get("attempts", 1)))
        return penalty

    def mark_completed(self, coverage):
        self.data["completed"] = True
        self.data["completion_version"] = 2
        self.data["completed_at"] = time.time()
        self.data["coverage"] = coverage
        self.data["completed_goal_count"] = len(self.data["visited_goals"])
        self.save()

    def mark_incomplete(self, coverage, reason):
        self.data["completed"] = False
        self.data.pop("completed_at", None)
        self.data.pop("completion_version", None)
        self.data["coverage"] = coverage
        self.data["incomplete_reason"] = reason
        self.save()


class FrontierExplorer(object):
    def __init__(self, args):
        self.args = args
        self.map_lock = threading.Lock()
        self.map_message = None
        self.scan_lock = threading.Lock()
        self.scan_sectors = None
        self.tf_listener = tf.TransformListener()
        self.cmd_pub = rospy.Publisher("/cmd_vel", Twist, queue_size=1)
        self.map_sub = rospy.Subscriber("/map", OccupancyGrid, self._map_callback, queue_size=1)
        self.scan_sub = rospy.Subscriber("/scan", LaserScan, self._scan_callback, queue_size=1)
        self.move_client = actionlib.SimpleActionClient("/move_base", MoveBaseAction)
        self.use_move_base = False
        self.make_plan = None
        self.clear_costmaps = None
        self.memory = ExplorationMemory(args.memory, args.map_name, args.reset_memory)
        self.started_at = time.time()
        self.goal_count = 0
        self.last_coverage = 0.0

    def _map_callback(self, message):
        with self.map_lock:
            self.map_message = message

    @staticmethod
    def _minimum_scan(values):
        valid = [value for value in values if is_finite(value) and 0.05 < value < 20.0]
        return min(valid) if valid else 20.0

    def _scan_callback(self, message):
        front = []
        left = []
        right = []
        angle = message.angle_min
        for value in message.ranges:
            degrees = math.degrees(angle)
            if -30.0 <= degrees <= 30.0:
                front.append(value)
            elif 30.0 < degrees <= 140.0:
                left.append(value)
            elif -140.0 <= degrees < -30.0:
                right.append(value)
            angle += message.angle_increment
        with self.scan_lock:
            self.scan_sectors = {
                "front": self._minimum_scan(front),
                "left": self._minimum_scan(left),
                "right": self._minimum_scan(right),
            }

    def current_scan(self):
        with self.scan_lock:
            return dict(self.scan_sectors) if self.scan_sectors else None

    def bootstrap_mapping(self):
        rospy.logwarn(
            "[BOOTSTRAP] No map message yet; performing %.1fs safe calibration rotation",
            self.args.bootstrap_spin_seconds,
        )
        command = Twist()
        command.angular.z = self.args.bootstrap_angular_speed
        stop = Twist()
        rate = rospy.Rate(10)
        deadline = time.time() + self.args.bootstrap_spin_seconds
        try:
            while not rospy.is_shutdown() and time.time() < deadline:
                command.angular.z = self.args.bootstrap_angular_speed
                self.cmd_pub.publish(command)
                rate.sleep()
        finally:
            for _ in range(10):
                self.cmd_pub.publish(stop)
                rate.sleep()

    def scan_in_place(self, seconds, angular_speed=None):
        duration = max(0.0, float(seconds))
        if duration <= 0.0:
            return
        spin_speed = self.args.post_goal_scan_speed if angular_speed is None else float(angular_speed)
        command = Twist()
        command.angular.z = spin_speed
        stop = Twist()
        rate = rospy.Rate(10)
        deadline = time.time() + duration
        last_record = 0.0
        while not rospy.is_shutdown() and time.time() < deadline:
            self.cmd_pub.publish(command)
            now = time.time()
            if now - last_record >= 2.0:
                try:
                    self.memory.record_position(self.robot_pose(timeout=0.4), self.args.path_memory_spacing)
                except Exception:
                    pass
                last_record = now
            rate.sleep()
        for _ in range(10):
            self.cmd_pub.publish(stop)
            rate.sleep()

    def wait_until_ready(self):
        rospy.loginfo("[START] Waiting for live LiDAR and gmapping data")
        deadline = time.time() + self.args.startup_timeout
        scan_ready = False
        while not rospy.is_shutdown() and time.time() < deadline:
            try:
                rospy.wait_for_message("/scan", LaserScan, timeout=1.0)
                scan_ready = True
                break
            except Exception:
                rospy.sleep(0.2)
        if not scan_ready:
            rospy.logwarn(
                "[START] No live /scan message received yet; continuing with fallback LiDAR safety values"
            )

        ready = False
        map_wait_deadline = time.time() + min(5.0, self.args.startup_timeout)
        while not rospy.is_shutdown() and time.time() < map_wait_deadline:
            with self.map_lock:
                ready = self.map_message is not None
            if ready:
                break
            rospy.sleep(0.2)
        if not ready:
            self.bootstrap_mapping()

        ready = False
        deadline = time.time() + self.args.startup_timeout
        while not rospy.is_shutdown() and time.time() < deadline:
            with self.map_lock:
                ready = self.map_message is not None
            if ready:
                break
            rospy.sleep(0.2)
        if not ready:
            raise RuntimeError("gmapping produced no /map message after bootstrap rotation")

        self.use_move_base = self.move_client.wait_for_server(rospy.Duration(4.0))
        if self.use_move_base:
            rospy.loginfo("[START] move_base action navigation is available")
        else:
            rospy.logwarn("[START] move_base unavailable; using internal A* path following")

        if self.use_move_base and self.args.make_plan_service:
            try:
                rospy.wait_for_service(
                    self.args.make_plan_service,
                    timeout=min(8.0, self.args.startup_timeout),
                )
                self.make_plan = rospy.ServiceProxy(self.args.make_plan_service, GetPlan)
                rospy.loginfo("[START] Using route service %s", self.args.make_plan_service)
            except Exception as exc:
                rospy.logwarn(
                    "[START] Route service %s is unavailable (%s); using geometric ranking",
                    self.args.make_plan_service,
                    exc,
                )
        try:
            rospy.wait_for_service("/move_base/clear_costmaps", timeout=3.0)
            self.clear_costmaps = rospy.ServiceProxy("/move_base/clear_costmaps", Empty)
        except Exception:
            self.clear_costmaps = None
        self.robot_pose(timeout=self.args.startup_timeout)
        rospy.loginfo("[START] Professional frontier explorer is ready")

    def robot_pose(self, timeout=2.0):
        deadline = time.time() + timeout
        last_error = None
        while not rospy.is_shutdown() and time.time() < deadline:
            for base_frame in ("/base_footprint", "/base_link"):
                try:
                    translation, rotation = self.tf_listener.lookupTransform(
                        "/map", base_frame, rospy.Time(0)
                    )
                    yaw = tf.transformations.euler_from_quaternion(rotation)[2]
                    return {"x": translation[0], "y": translation[1], "yaw": yaw}
                except Exception as exc:
                    last_error = exc
            rospy.sleep(0.1)
        raise RuntimeError("map to robot TF unavailable: %s" % last_error)

    @staticmethod
    def _shift(mask, row_offset, column_offset):
        shifted = np.zeros_like(mask, dtype=np.bool_)
        source_rows = slice(max(0, -row_offset), mask.shape[0] - max(0, row_offset))
        source_cols = slice(max(0, -column_offset), mask.shape[1] - max(0, column_offset))
        target_rows = slice(max(0, row_offset), mask.shape[0] - max(0, -row_offset))
        target_cols = slice(max(0, column_offset), mask.shape[1] - max(0, -column_offset))
        shifted[target_rows, target_cols] = mask[source_rows, source_cols]
        return shifted

    def map_snapshot(self):
        with self.map_lock:
            message = self.map_message
        if message is None:
            raise RuntimeError("map is unavailable")
        width = int(message.info.width)
        height = int(message.info.height)
        grid = np.asarray(message.data, dtype=np.int16).reshape((height, width))
        quaternion = message.info.origin.orientation
        origin_yaw = tf.transformations.euler_from_quaternion((
            quaternion.x, quaternion.y, quaternion.z, quaternion.w
        ))[2]
        return {
            "grid": grid,
            "resolution": float(message.info.resolution),
            "origin_x": float(message.info.origin.position.x),
            "origin_y": float(message.info.origin.position.y),
            "origin_yaw": origin_yaw,
            "frame": message.header.frame_id or "map",
        }

    @staticmethod
    def grid_to_world(snapshot, row, column):
        local_x = (float(column) + 0.5) * snapshot["resolution"]
        local_y = (float(row) + 0.5) * snapshot["resolution"]
        cosine = math.cos(snapshot["origin_yaw"])
        sine = math.sin(snapshot["origin_yaw"])
        return (
            snapshot["origin_x"] + cosine * local_x - sine * local_y,
            snapshot["origin_y"] + sine * local_x + cosine * local_y,
        )

    @staticmethod
    def world_to_grid(snapshot, x, y):
        delta_x = float(x) - snapshot["origin_x"]
        delta_y = float(y) - snapshot["origin_y"]
        cosine = math.cos(-snapshot["origin_yaw"])
        sine = math.sin(-snapshot["origin_yaw"])
        local_x = cosine * delta_x - sine * delta_y
        local_y = sine * delta_x + cosine * delta_y
        column = int(math.floor(local_x / snapshot["resolution"]))
        row = int(math.floor(local_y / snapshot["resolution"]))
        return row, column

    @staticmethod
    def _nearest_walkable(walkable, row, column, radius=12):
        height, width = walkable.shape
        if 0 <= row < height and 0 <= column < width and walkable[row, column]:
            return row, column
        best = None
        best_squared = float("inf")
        for candidate_row in range(max(0, row - radius), min(height, row + radius + 1)):
            for candidate_column in range(max(0, column - radius), min(width, column + radius + 1)):
                if not walkable[candidate_row, candidate_column]:
                    continue
                squared = (candidate_row - row) ** 2 + (candidate_column - column) ** 2
                if squared < best_squared:
                    best = (candidate_row, candidate_column)
                    best_squared = squared
        return best

    def astar_plan(self, snapshot, robot, candidate):
        grid = snapshot["grid"]
        resolution = snapshot["resolution"]
        free = np.logical_and(grid >= 0, grid <= self.args.free_threshold)
        non_obstacle = (grid < self.args.occupied_threshold).astype(np.uint8)
        clearance = cv2.distanceTransform(non_obstacle, cv2.DIST_L2, 5) * resolution
        walkable = np.logical_and(free, clearance >= self.args.minimum_clearance)

        start = self._nearest_walkable(
            walkable,
            *self.world_to_grid(snapshot, robot["x"], robot["y"])
        )
        goal = self._nearest_walkable(
            walkable,
            int(candidate["row"]),
            int(candidate["column"]),
        )
        if start is None or goal is None:
            return None

        height, width = walkable.shape
        total_cells = height * width
        start_index = start[0] * width + start[1]
        goal_index = goal[0] * width + goal[1]
        costs = np.full(total_cells, np.inf, dtype=np.float64)
        previous = np.full(total_cells, -1, dtype=np.int64)
        costs[start_index] = 0.0
        queue = [(0.0, 0.0, start[0], start[1])]
        expanded = 0
        neighbor_steps = (
            (-1, 0, 1.0), (1, 0, 1.0), (0, -1, 1.0), (0, 1, 1.0),
            (-1, -1, math.sqrt(2.0)), (-1, 1, math.sqrt(2.0)),
            (1, -1, math.sqrt(2.0)), (1, 1, math.sqrt(2.0)),
        )

        while queue and expanded < self.args.astar_max_expansions:
            _, current_cost, row, column = heapq.heappop(queue)
            current_index = row * width + column
            if current_cost > costs[current_index] + 1e-9:
                continue
            if current_index == goal_index:
                break
            expanded += 1
            for row_delta, column_delta, step_cost in neighbor_steps:
                next_row = row + row_delta
                next_column = column + column_delta
                if not (0 <= next_row < height and 0 <= next_column < width):
                    continue
                if not walkable[next_row, next_column]:
                    continue
                if row_delta != 0 and column_delta != 0:
                    if not walkable[row, next_column] or not walkable[next_row, column]:
                        continue
                next_index = next_row * width + next_column
                next_cost = current_cost + step_cost
                if next_cost >= costs[next_index]:
                    continue
                costs[next_index] = next_cost
                previous[next_index] = current_index
                heuristic = math.hypot(goal[0] - next_row, goal[1] - next_column)
                heapq.heappush(queue, (next_cost + heuristic, next_cost, next_row, next_column))

        if not is_finite(costs[goal_index]):
            return None

        cell_path = []
        current = goal_index
        while current >= 0:
            row = int(current // width)
            column = int(current % width)
            cell_path.append((row, column))
            if current == start_index:
                break
            current = int(previous[current])
        if not cell_path or cell_path[-1] != start:
            return None
        cell_path.reverse()

        sample_step = max(1, int(round(self.args.path_waypoint_spacing / resolution)))
        sampled = cell_path[::sample_step]
        if sampled[-1] != cell_path[-1]:
            sampled.append(cell_path[-1])
        world_path = [self.grid_to_world(snapshot, row, column) for row, column in sampled]
        return {
            "length": float(costs[goal_index]) * resolution,
            "points": world_path,
            "expanded": expanded,
        }

    def frontier_candidates(self, snapshot, robot):
        grid = snapshot["grid"]
        resolution = snapshot["resolution"]
        unknown = grid < 0
        free = np.logical_and(grid >= 0, grid <= self.args.free_threshold)
        adjacent_unknown = np.zeros_like(unknown, dtype=np.bool_)
        for row_offset in (-1, 0, 1):
            for column_offset in (-1, 0, 1):
                if row_offset == 0 and column_offset == 0:
                    continue
                adjacent_unknown |= self._shift(unknown, row_offset, column_offset)
        frontier = np.logical_and(free, adjacent_unknown)

        non_obstacle = (grid < self.args.occupied_threshold).astype(np.uint8)
        obstacle_clearance = cv2.distanceTransform(non_obstacle, cv2.DIST_L2, 5) * resolution
        frontier &= obstacle_clearance >= self.args.minimum_clearance

        count, labels, stats, centroids = cv2.connectedComponentsWithStats(
            frontier.astype(np.uint8), connectivity=8
        )
        minimum_cells = max(3, int(math.ceil(self.args.minimum_frontier_length / resolution)))
        gain_radius = max(2, int(round(self.args.gain_radius / resolution)))
        candidates = []

        for label in range(1, count):
            cells = int(stats[label, cv2.CC_STAT_AREA])
            if cells < minimum_cells:
                continue
            rows, columns = np.where(labels == label)
            if len(rows) == 0:
                continue
            clearances = obstacle_clearance[rows, columns]
            best_clearance = float(np.max(clearances))
            safe_indexes = np.where(clearances >= max(self.args.minimum_clearance, best_clearance * 0.75))[0]
            centroid_column, centroid_row = centroids[label]
            first_index = min(
                safe_indexes,
                key=lambda index: (
                    (float(rows[index]) - centroid_row) ** 2 +
                    (float(columns[index]) - centroid_column) ** 2
                ),
            )
            representative_indexes = [first_index]
            separation_cells = max(2.0, self.args.frontier_goal_spacing / resolution)
            while len(representative_indexes) < self.args.maximum_goals_per_frontier:
                minimum_squared = np.full(len(safe_indexes), np.inf)
                for chosen in representative_indexes:
                    squared = (
                        (rows[safe_indexes].astype(np.float64) - float(rows[chosen])) ** 2 +
                        (columns[safe_indexes].astype(np.float64) - float(columns[chosen])) ** 2
                    )
                    minimum_squared = np.minimum(minimum_squared, squared)
                farthest_position = int(np.argmax(minimum_squared))
                if math.sqrt(float(minimum_squared[farthest_position])) < separation_cells:
                    break
                representative_indexes.append(int(safe_indexes[farthest_position]))

            for best_index in representative_indexes:
                row = int(rows[best_index])
                column = int(columns[best_index])
                x, y = self.grid_to_world(snapshot, row, column)
                candidate = {"x": x, "y": y}
                if self.memory.visited_near(candidate, self.args.visited_radius):
                    continue
                failure_count = self.memory.failure_penalty(candidate)
                if failure_count >= self.args.maximum_failures:
                    continue

                row_start = max(0, row - gain_radius)
                row_end = min(grid.shape[0], row + gain_radius + 1)
                col_start = max(0, column - gain_radius)
                col_end = min(grid.shape[1], column + gain_radius + 1)
                local_unknown = unknown[row_start:row_end, col_start:col_end]
                local_frontier = frontier[row_start:row_end, col_start:col_end]
                unknown_cells = int(np.count_nonzero(local_unknown))
                frontier_cells = int(np.count_nonzero(local_frontier))
                candidate.update({
                    "row": row,
                    "column": column,
                    "frontier_cells": frontier_cells,
                    "gain": unknown_cells * resolution * resolution,
                    "straight_distance": distance(candidate, robot),
                    "failure_count": failure_count,
                    "clearance": float(obstacle_clearance[row, column]),
                })
                candidates.append(candidate)

        known = int(np.count_nonzero(grid >= 0))
        coverage = 100.0 * known / float(grid.size) if grid.size else 0.0
        self.last_coverage = coverage
        return candidates, coverage, int(np.count_nonzero(frontier))

    def pose_stamped(self, x, y, yaw):
        pose = PoseStamped()
        pose.header.frame_id = "map"
        pose.header.stamp = rospy.Time.now()
        pose.pose.position.x = x
        pose.pose.position.y = y
        quaternion = tf.transformations.quaternion_from_euler(0.0, 0.0, yaw)
        pose.pose.orientation.x = quaternion[0]
        pose.pose.orientation.y = quaternion[1]
        pose.pose.orientation.z = quaternion[2]
        pose.pose.orientation.w = quaternion[3]
        return pose

    def plan_to(self, snapshot, robot, candidate):
        if (
            self.use_move_base
            and self.make_plan is not None
            and os.environ.get("AUTO_SCAN_USE_MOVE_BASE", "1") != "0"
        ):
            request = GetPlanRequest()
            request.start = self.pose_stamped(robot["x"], robot["y"], robot["yaw"])
            yaw = math.atan2(candidate["y"] - robot["y"], candidate["x"] - robot["x"])
            request.goal = self.pose_stamped(candidate["x"], candidate["y"], yaw)
            request.tolerance = self.args.plan_tolerance
            try:
                response = self.make_plan(request)
                if len(response.plan.poses) >= 2:
                    service_length = path_length(response.plan.poses)
                    candidate["service_path_length"] = service_length
                    return service_length
            except Exception as exc:
                rospy.logwarn("[PLAN] make_plan failed: %s", exc)

        direct_plan = self.astar_plan(snapshot, robot, candidate)
        if direct_plan is None:
            return None
        candidate["direct_path"] = direct_plan["points"]
        candidate["astar_expanded"] = direct_plan["expanded"]
        return direct_plan["length"]

    def choose_goal(self, snapshot, candidates, robot):
        preliminary = sorted(
            candidates,
            key=lambda candidate: -(
                candidate["gain"] + 0.04 * candidate["frontier_cells"]
            ) / max(0.5, candidate["straight_distance"]),
        )[:self.args.plan_candidates]
        planned = []
        for candidate in preliminary:
            if candidate["straight_distance"] < self.args.minimum_goal_distance:
                continue
            length = self.plan_to(snapshot, robot, candidate)
            if length is None:
                self.memory.mark_failed(candidate)
                continue
            candidate["path_length"] = length
            candidate["score"] = (
                self.args.gain_weight * math.log1p(candidate["gain"] * 10.0 + candidate["frontier_cells"])
                - self.args.distance_weight * length
                - self.args.failure_weight * candidate["failure_count"]
            )
            planned.append(candidate)
        if not planned and self.args.allow_near_goals:
            rospy.logwarn(
                "[PLAN] No frontier candidate met the minimum travel distance of %.2fm; accepting close startup frontiers",
                self.args.minimum_goal_distance,
            )
            for candidate in preliminary:
                if candidate["straight_distance"] < self.args.minimum_near_goal_distance:
                    continue
                length = self.plan_to(snapshot, robot, candidate)
                if length is None:
                    self.memory.mark_failed(candidate)
                    continue
                candidate["near_goal"] = True
                candidate["path_length"] = length
                candidate["score"] = (
                    self.args.gain_weight * math.log1p(candidate["gain"] * 10.0 + candidate["frontier_cells"])
                    - self.args.distance_weight * length
                    - self.args.failure_weight * candidate["failure_count"]
                )
                planned.append(candidate)
        if not planned:
            rospy.logwarn(
                "[PLAN] No reachable frontier is far enough to drive to; using active recovery instead"
            )
            return None
        return max(planned, key=lambda candidate: candidate["score"])

    def stop_robot(self):
        stop = Twist()
        for _ in range(8):
            self.cmd_pub.publish(stop)
            rospy.sleep(0.05)

    def clear_navigation_costmaps(self):
        if self.clear_costmaps is None:
            return
        try:
            self.clear_costmaps()
        except Exception as exc:
            rospy.logwarn("[RECOVERY] Could not clear costmaps: %s", exc)

    def recovery_probe(self, spin_direction):
        rospy.loginfo("[RECOVERY] Probing safely to expand the gmapping map")
        self.scan_in_place(
            self.args.recovery_turn_seconds,
            spin_direction * self.args.post_goal_scan_speed,
        )
        scan = self.current_scan() or {"front": 0.0, "left": 0.0, "right": 0.0}
        if scan["front"] < self.args.recovery_min_front_clearance:
            turn_direction = 1.0 if scan.get("left", 0.0) >= scan.get("right", 0.0) else -1.0
            rospy.loginfo(
                "[RECOVERY] Front clearance %.2fm is below %.2fm; turning toward clearer side",
                scan["front"],
                self.args.recovery_min_front_clearance,
            )
            self.scan_in_place(
                self.args.recovery_turn_seconds,
                turn_direction * self.args.post_goal_scan_speed,
            )
            scan = self.current_scan() or scan
        if scan["front"] < self.args.recovery_min_front_clearance:
            rospy.logwarn(
                "[RECOVERY] Not driving forward; front clearance %.2fm is too small",
                scan["front"],
            )
            self.stop_robot()
            return False

        command = Twist()
        command.linear.x = min(
            self.args.recovery_drive_speed,
            max(0.05, self.args.direct_max_linear * 0.75),
        )
        stop = Twist()
        rate = rospy.Rate(10)
        deadline = time.time() + self.args.recovery_drive_seconds
        last_record = 0.0
        rospy.loginfo(
            "[RECOVERY] Driving forward cautiously for %.1fs at %.2fm/s",
            self.args.recovery_drive_seconds,
            command.linear.x,
        )
        while not rospy.is_shutdown() and time.time() < deadline:
            scan = self.current_scan() or {"front": 0.0}
            if scan["front"] < self.args.obstacle_stop_distance:
                rospy.logwarn(
                    "[RECOVERY] Stopping probe; obstacle at %.2fm",
                    scan["front"],
                )
                break
            self.cmd_pub.publish(command)
            now = time.time()
            if now - last_record >= 1.0:
                try:
                    self.memory.record_position(
                        self.robot_pose(timeout=0.4),
                        self.args.path_memory_spacing,
                    )
                except Exception:
                    pass
                last_record = now
            rate.sleep()
        for _ in range(10):
            self.cmd_pub.publish(stop)
            rate.sleep()
        return True

    def navigate_move_base(self, goal):
        robot = self.robot_pose()
        yaw = math.atan2(goal["y"] - robot["y"], goal["x"] - robot["x"])
        move_goal = MoveBaseGoal()
        move_goal.target_pose = self.pose_stamped(goal["x"], goal["y"], yaw)
        self.move_client.send_goal(move_goal)
        started = time.time()
        last_progress = started
        best_distance = distance(robot, goal)
        last_record = 0.0

        while not rospy.is_shutdown():
            state = self.move_client.get_state()
            if state == GoalStatus.SUCCEEDED:
                self.stop_robot()
                return True, "succeeded"
            if state in (
                GoalStatus.PREEMPTED,
                GoalStatus.ABORTED,
                GoalStatus.REJECTED,
                GoalStatus.RECALLED,
                GoalStatus.LOST,
            ):
                self.stop_robot()
                return False, "move_base state %d" % state

            now = time.time()
            if now - started >= self.args.goal_timeout:
                self.move_client.cancel_goal()
                self.stop_robot()
                return False, "goal timeout"
            try:
                current = self.robot_pose(timeout=0.5)
                current_distance = distance(current, goal)
                if current_distance <= self.args.goal_tolerance:
                    self.move_client.cancel_goal()
                    self.stop_robot()
                    return True, "goal tolerance reached"
                if best_distance - current_distance >= self.args.progress_distance:
                    best_distance = current_distance
                    last_progress = now
                if now - last_record >= 2.0:
                    self.memory.record_position(current, self.args.path_memory_spacing)
                    last_record = now
            except Exception:
                pass

            if now - last_progress >= self.args.progress_timeout:
                self.move_client.cancel_goal()
                self.stop_robot()
                self.clear_navigation_costmaps()
                return False, "no navigation progress"
            rospy.sleep(0.4)

        self.move_client.cancel_goal()
        self.stop_robot()
        return False, "ROS shutdown"

    @staticmethod
    def _normalize_angle(angle):
        while angle > math.pi:
            angle -= 2.0 * math.pi
        while angle < -math.pi:
            angle += 2.0 * math.pi
        return angle

    def navigate_direct(self, goal):
        points = list(goal.get("direct_path") or [])
        if not points:
            return False, "internal A* path is empty"
        started = time.time()
        last_progress = started
        last_pose = self.robot_pose()
        waypoint_index = 0
        last_record = 0.0
        rate = rospy.Rate(10)

        while not rospy.is_shutdown():
            now = time.time()
            if now - started >= self.args.goal_timeout:
                self.stop_robot()
                return False, "direct path timeout"
            try:
                robot = self.robot_pose(timeout=0.4)
            except Exception:
                self.stop_robot()
                return False, "robot pose unavailable during direct path"

            if distance(robot, goal) <= self.args.goal_tolerance:
                self.stop_robot()
                return True, "A* goal tolerance reached"

            if distance(robot, last_pose) >= self.args.progress_distance:
                last_progress = now
                last_pose = robot
            if now - last_progress >= self.args.progress_timeout:
                self.stop_robot()
                return False, "no progress following A* path"

            while waypoint_index < len(points) - 1:
                point = {"x": points[waypoint_index][0], "y": points[waypoint_index][1]}
                if distance(robot, point) > self.args.path_waypoint_tolerance:
                    break
                waypoint_index += 1
            target_x, target_y = points[min(waypoint_index, len(points) - 1)]
            desired = math.atan2(target_y - robot["y"], target_x - robot["x"])
            heading_error = self._normalize_angle(desired - robot["yaw"])
            scan = self.current_scan() or {"front": 20.0, "left": 20.0, "right": 20.0}

            command = Twist()
            if scan["front"] < self.args.obstacle_stop_distance and abs(heading_error) < 0.75:
                command.linear.x = 0.0
                command.angular.z = (
                    self.args.direct_max_angular
                    if scan["left"] >= scan["right"]
                    else -self.args.direct_max_angular
                )
            else:
                command.angular.z = max(
                    -self.args.direct_max_angular,
                    min(self.args.direct_max_angular, self.args.heading_gain * heading_error),
                )
                if abs(heading_error) <= self.args.drive_heading_limit:
                    speed = self.args.direct_max_linear
                    if scan["front"] < self.args.obstacle_slow_distance:
                        span = max(0.05, self.args.obstacle_slow_distance - self.args.obstacle_stop_distance)
                        factor = (scan["front"] - self.args.obstacle_stop_distance) / span
                        speed *= max(0.25, min(1.0, factor))
                    command.linear.x = speed

            self.cmd_pub.publish(command)
            if now - last_record >= 2.0:
                self.memory.record_position(robot, self.args.path_memory_spacing)
                last_record = now
            rate.sleep()

        self.stop_robot()
        return False, "ROS shutdown"

    def navigate(self, goal):
        if self.use_move_base and os.environ.get("AUTO_SCAN_USE_MOVE_BASE", "1") != "0":
            return self.navigate_move_base(goal)
        return self.navigate_direct(goal)

    def has_completion_evidence(self, coverage):
        visited = len(self.memory.data["visited_goals"])
        return (
            coverage >= self.args.minimum_completion_coverage
            and visited >= self.args.minimum_completion_goals
        )

    def run(self):
        self.wait_until_ready()
        if self.memory.data.get("completed") and not self.args.reset_memory:
            rospy.loginfo("[COMPLETE] This map is already marked complete in exploration memory")
            return 0

        no_goal_cycles = 0
        weak_expansion_cycles = 0
        while not rospy.is_shutdown():
            if self.args.maximum_runtime > 0 and time.time() - self.started_at >= self.args.maximum_runtime:
                rospy.logwarn("[STOP] Maximum exploration runtime reached; saving partial map")
                return 2
            if self.args.maximum_goals > 0 and self.goal_count >= self.args.maximum_goals:
                rospy.logwarn("[STOP] Maximum exploration goal count reached; saving partial map")
                return 2

            snapshot = self.map_snapshot()
            robot = self.robot_pose()
            self.memory.record_position(robot, self.args.path_memory_spacing)
            candidates, coverage, frontier_cells = self.frontier_candidates(snapshot, robot)
            rospy.loginfo(
                "[SCAN] coverage=%.2f%% frontier_cells=%d new_regions=%d visited=%d failed=%d",
                coverage,
                frontier_cells,
                len(candidates),
                len(self.memory.data["visited_goals"]),
                len(self.memory.data["failed_goals"]),
            )
            goal = self.choose_goal(snapshot, candidates, robot) if candidates else None
            if goal is None:
                no_goal_cycles += 1
                rospy.loginfo(
                    "[COMPLETE CHECK] No reachable unvisited frontier (%d/%d)",
                    no_goal_cycles,
                    self.args.completion_confirmations,
                )
                if no_goal_cycles >= self.args.completion_confirmations:
                    self.stop_robot()
                    if self.has_completion_evidence(coverage):
                        self.memory.mark_completed(coverage)
                        rospy.loginfo("[COMPLETE] No reachable unvisited frontiers remain")
                        return 0
                    weak_expansion_cycles += 1
                    reason = (
                        "insufficient exploration evidence: coverage %.2f%%, "
                        "%d successful goals" % (
                            coverage,
                            len(self.memory.data["visited_goals"]),
                        )
                    )
                    self.memory.mark_incomplete(coverage, reason)
                    if weak_expansion_cycles >= self.args.max_weak_expansion_cycles:
                        rospy.logwarn(
                            "[PARTIAL] %s after %d bounded expansion attempts; saving instead of looping",
                            reason,
                            weak_expansion_cycles,
                        )
                        return 2
                    rospy.logwarn(
                        "[EXPAND] %s; bounded expansion attempt %d/%d",
                        reason,
                        weak_expansion_cycles,
                        self.args.max_weak_expansion_cycles,
                    )
                    no_goal_cycles = 0
                self.clear_navigation_costmaps()
                spin_direction = 1.0 if no_goal_cycles % 2 else -1.0
                self.recovery_probe(spin_direction)
                rospy.sleep(self.args.completion_check_delay)
                continue

            no_goal_cycles = 0
            weak_expansion_cycles = 0
            self.goal_count += 1
            rospy.loginfo(
                "[GOAL %d] x=%.2f y=%.2f path=%.2fm gain=%.2f score=%.2f",
                self.goal_count,
                goal["x"],
                goal["y"],
                goal["path_length"],
                goal["gain"],
                goal["score"],
            )
            success, reason = self.navigate(goal)
            if success:
                rospy.loginfo("[GOAL] Region scanned: %s", reason)
                self.memory.mark_visited(goal)
                if self.args.post_goal_scan_seconds > 0.0:
                    self.scan_in_place(self.args.post_goal_scan_seconds)
            else:
                rospy.logwarn("[GOAL] Region failed: %s", reason)
                self.memory.mark_failed(goal)
                self.clear_navigation_costmaps()
            rospy.sleep(self.args.map_settle_time)
        return 1


def parse_args():
    parser = argparse.ArgumentParser(description="Persistent ROS frontier explorer")
    parser.add_argument("--map-name", required=True)
    parser.add_argument("--memory", required=True)
    parser.add_argument("--make-plan-service", default="")
    parser.add_argument("--reset-memory", action="store_true")
    parser.add_argument("--startup-timeout", type=float, default=35.0)
    parser.add_argument("--bootstrap-spin-seconds", type=float, default=12.0)
    parser.add_argument("--bootstrap-angular-speed", type=float, default=0.45)
    parser.add_argument("--free-threshold", type=int, default=20)
    parser.add_argument("--occupied-threshold", type=int, default=65)
    parser.add_argument("--minimum-frontier-length", type=float, default=0.35)
    parser.add_argument("--minimum-clearance", type=float, default=0.35)
    parser.add_argument("--gain-radius", type=float, default=1.5)
    parser.add_argument("--frontier-goal-spacing", type=float, default=1.5)
    parser.add_argument("--maximum-goals-per-frontier", type=int, default=8)
    parser.add_argument("--visited-radius", type=float, default=0.85)
    parser.add_argument("--maximum-failures", type=int, default=2)
    parser.add_argument("--plan-candidates", type=int, default=6)
    parser.add_argument("--plan-tolerance", type=float, default=0.30)
    parser.add_argument("--astar-max-expansions", type=int, default=200000)
    parser.add_argument("--path-waypoint-spacing", type=float, default=0.30)
    parser.add_argument("--path-waypoint-tolerance", type=float, default=0.22)
    parser.add_argument("--gain-weight", type=float, default=2.2)
    parser.add_argument("--distance-weight", type=float, default=1.0)
    parser.add_argument("--failure-weight", type=float, default=3.0)
    parser.add_argument("--allow-near-goals", action="store_true")
    parser.add_argument("--goal-timeout", type=float, default=60.0)
    parser.add_argument("--goal-tolerance", type=float, default=0.35)
    parser.add_argument("--progress-timeout", type=float, default=15.0)
    parser.add_argument("--progress-distance", type=float, default=0.18)
    parser.add_argument("--minimum-goal-distance", type=float, default=0.10)
    parser.add_argument("--minimum-near-goal-distance", type=float, default=0.05)
    parser.add_argument("--direct-max-linear", type=float, default=0.35)
    parser.add_argument("--direct-max-angular", type=float, default=1.00)
    parser.add_argument("--heading-gain", type=float, default=1.5)
    parser.add_argument("--drive-heading-limit", type=float, default=0.60)
    parser.add_argument("--obstacle-stop-distance", type=float, default=0.55)
    parser.add_argument("--obstacle-slow-distance", type=float, default=0.90)
    parser.add_argument("--path-memory-spacing", type=float, default=0.65)
    parser.add_argument("--post-goal-scan-seconds", type=float, default=0.8)
    parser.add_argument("--post-goal-scan-speed", type=float, default=0.45)
    parser.add_argument("--recovery-scan-seconds", type=float, default=7.0)
    parser.add_argument("--recovery-turn-seconds", type=float, default=0.8)
    parser.add_argument("--recovery-drive-seconds", type=float, default=2.0)
    parser.add_argument("--recovery-drive-speed", type=float, default=0.25)
    parser.add_argument("--recovery-min-front-clearance", type=float, default=0.75)
    parser.add_argument("--completion-confirmations", type=int, default=3)
    parser.add_argument("--minimum-completion-coverage", type=float, default=0.40)
    parser.add_argument("--minimum-completion-goals", type=int, default=1)
    parser.add_argument("--completion-check-delay", type=float, default=1.0)
    parser.add_argument("--map-settle-time", type=float, default=0.6)
    parser.add_argument("--max-weak-expansion-cycles", type=int, default=2)
    parser.add_argument("--maximum-runtime", type=float, default=180.0)
    parser.add_argument("--maximum-goals", type=int, default=10)
    return parser.parse_args()


def main():
    if ROS_IMPORT_ERROR is not None:
        print("ROS imports are unavailable: %s" % ROS_IMPORT_ERROR)
        return 1
    args = parse_args()
    rospy.init_node("professional_frontier_explorer", anonymous=False)
    explorer = FrontierExplorer(args)

    def shutdown():
        try:
            explorer.move_client.cancel_all_goals()
            explorer.stop_robot()
            explorer.memory.save()
        except Exception:
            pass

    rospy.on_shutdown(shutdown)
    try:
        return explorer.run()
    except Exception as exc:
        rospy.logerr("[FATAL] Exploration stopped: %s", exc)
        return 1
    finally:
        shutdown()


if __name__ == "__main__":
    raise SystemExit(main())
