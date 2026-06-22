#!/usr/bin/env python3
"""ROS-side AMCL startup checks used by start_navigation.sh."""

import argparse
import json
import math
import sys
import time

import rospy
import tf2_ros
from geometry_msgs.msg import PoseWithCovarianceStamped, Twist
from nav_msgs.msg import OccupancyGrid
from sensor_msgs.msg import LaserScan
from std_srvs.srv import Empty


def finite_ranges(scan):
    return [
        value for value in scan.ranges
        if math.isfinite(value) and scan.range_min <= value <= scan.range_max
    ]


def lookup_base_transform(buffer, target_frame, timeout):
    errors = []
    for base_frame in ("base_footprint", "base_link"):
        try:
            transform = buffer.lookup_transform(
                target_frame,
                base_frame,
                rospy.Time(0),
                rospy.Duration(timeout),
            )
            return base_frame, transform
        except Exception as exc:
            errors.append(f"{base_frame}: {exc}")
    raise RuntimeError("; ".join(errors))


def preflight(args):
    map_msg = rospy.wait_for_message("/map", OccupancyGrid, timeout=args.timeout)
    if map_msg.info.width <= 0 or map_msg.info.height <= 0 or not map_msg.data:
        raise RuntimeError("/map is empty")

    scan = rospy.wait_for_message("/scan", LaserScan, timeout=args.timeout)
    valid = finite_ranges(scan)
    if len(valid) < args.min_ranges:
        raise RuntimeError(
            f"/scan has only {len(valid)} valid ranges; need at least {args.min_ranges}"
        )

    scan_frame = scan.header.frame_id.lstrip("/")
    if not scan_frame:
        raise RuntimeError("/scan has an empty frame_id")

    buffer = tf2_ros.Buffer(cache_time=rospy.Duration(20.0))
    listener = tf2_ros.TransformListener(buffer)
    rospy.sleep(0.5)
    base_frame, _ = lookup_base_transform(buffer, "odom", args.timeout)
    buffer.lookup_transform(
        base_frame,
        scan_frame,
        rospy.Time(0),
        rospy.Duration(args.timeout),
    )
    print(json.dumps({
        "map_width": map_msg.info.width,
        "map_height": map_msg.info.height,
        "map_resolution": map_msg.info.resolution,
        "valid_scan_ranges": len(valid),
        "scan_frame": scan_frame,
        "base_frame": base_frame,
    }, sort_keys=True))
    print("AMCL_PREFLIGHT_OK")


def publish_initial_pose(args):
    publisher = rospy.Publisher("/initialpose", PoseWithCovarianceStamped, queue_size=1, latch=True)
    deadline = time.monotonic() + args.timeout
    while publisher.get_num_connections() == 0 and time.monotonic() < deadline:
        rospy.sleep(0.1)
    if publisher.get_num_connections() == 0:
        raise RuntimeError("AMCL is not subscribed to /initialpose")

    message = PoseWithCovarianceStamped()
    message.header.frame_id = "map"
    message.pose.pose.position.x = args.x
    message.pose.pose.position.y = args.y
    message.pose.pose.orientation.z = math.sin(args.yaw / 2.0)
    message.pose.pose.orientation.w = math.cos(args.yaw / 2.0)
    message.pose.covariance[0] = args.xy_std ** 2
    message.pose.covariance[7] = args.xy_std ** 2
    message.pose.covariance[35] = args.yaw_std ** 2

    rate = rospy.Rate(5)
    for _ in range(10):
        message.header.stamp = rospy.Time.now()
        publisher.publish(message)
        rate.sleep()
    request_nomotion_update(timeout=2.0)
    print(
        "AMCL_INITIAL_POSE_SENT "
        f"x={args.x:.3f} y={args.y:.3f} yaw={args.yaw:.3f}"
    )


def request_nomotion_update(timeout=1.0):
    try:
        rospy.wait_for_service("/request_nomotion_update", timeout=timeout)
        rospy.ServiceProxy("/request_nomotion_update", Empty)()
        return True
    except Exception:
        return False


def spin_robot(args):
    publisher = rospy.Publisher("/cmd_vel", Twist, queue_size=1)
    deadline = time.monotonic() + 3.0
    while publisher.get_num_connections() == 0 and time.monotonic() < deadline:
        rospy.sleep(0.1)
    if publisher.get_num_connections() == 0:
        raise RuntimeError("no subscriber is connected to /cmd_vel")

    command = Twist()
    command.angular.z = max(-0.8, min(0.8, args.angular))
    if abs(command.angular.z) < 0.2:
        raise RuntimeError("spin angular velocity must have magnitude >= 0.2 rad/s")
    stop = Twist()
    rate = rospy.Rate(15)
    end_time = time.monotonic() + max(1.0, args.duration)
    try:
        while time.monotonic() < end_time and not rospy.is_shutdown():
            publisher.publish(command)
            rate.sleep()
    finally:
        for _ in range(12):
            publisher.publish(stop)
            rate.sleep()
    print(
        "AMCL_SPIN_COMPLETE "
        f"duration={args.duration:.2f}s angular={command.angular.z:.3f}rad/s"
    )


def pose_quality(message):
    covariance = message.pose.covariance
    position_std = math.sqrt(max(0.0, covariance[0], covariance[7]))
    yaw_std = math.sqrt(max(0.0, covariance[35]))
    pose = message.pose.pose
    finite = all(math.isfinite(value) for value in (
        pose.position.x,
        pose.position.y,
        pose.orientation.z,
        pose.orientation.w,
        position_std,
        yaw_std,
    ))
    return finite, position_std, yaw_std


def check_convergence(args):
    buffer = tf2_ros.Buffer(cache_time=rospy.Duration(20.0))
    listener = tf2_ros.TransformListener(buffer)
    deadline = time.monotonic() + args.timeout
    consecutive_good = 0
    last_report = "no /amcl_pose received"
    pose_state = {"message": None, "count": 0}

    def pose_callback(message):
        pose_state["message"] = message
        pose_state["count"] += 1

    subscriber = rospy.Subscriber(
        "/amcl_pose",
        PoseWithCovarianceStamped,
        pose_callback,
        queue_size=5,
    )
    rospy.sleep(0.2)

    while time.monotonic() < deadline and not rospy.is_shutdown():
        previous_count = pose_state["count"]
        request_nomotion_update(timeout=0.3)
        sample_deadline = min(deadline, time.monotonic() + 2.0)
        while pose_state["count"] <= previous_count and time.monotonic() < sample_deadline:
            rospy.sleep(0.05)

        message = pose_state["message"]
        if message is not None and pose_state["count"] > previous_count:
            finite, position_std, yaw_std = pose_quality(message)
            tf_ok = False
            base_frame = "unknown"
            try:
                base_frame, _ = lookup_base_transform(buffer, "map", 0.5)
                tf_ok = True
            except Exception:
                pass
            last_report = (
                f"position_std={position_std:.3f}m yaw_std={yaw_std:.3f}rad "
                f"map_tf={tf_ok} base={base_frame}"
            )
            if finite and tf_ok and position_std <= args.max_position_std and yaw_std <= args.max_yaw_std:
                consecutive_good += 1
                if consecutive_good >= args.samples:
                    pose = message.pose.pose
                    print(
                        "AMCL_CONVERGED "
                        f"x={pose.position.x:.3f} y={pose.position.y:.3f} {last_report}"
                    )
                    return
            else:
                consecutive_good = 0
            rospy.sleep(0.35)
        else:
            consecutive_good = 0

    subscriber.unregister()
    print(f"AMCL_NOT_CONVERGED {last_report}")
    raise RuntimeError("AMCL covariance or map transform did not converge")


def snapshot(args):
    pose_state = {"message": None}

    def pose_callback(message):
        pose_state["message"] = message

    subscriber = rospy.Subscriber(
        "/amcl_pose",
        PoseWithCovarianceStamped,
        pose_callback,
        queue_size=1,
    )
    rospy.sleep(0.2)
    request_nomotion_update(timeout=1.0)
    deadline = time.monotonic() + args.timeout
    while pose_state["message"] is None and time.monotonic() < deadline:
        rospy.sleep(0.05)
    message = pose_state["message"]
    subscriber.unregister()
    if message is None:
        raise RuntimeError("no /amcl_pose received after no-motion update")
    finite, position_std, yaw_std = pose_quality(message)
    if not finite or position_std > args.max_position_std or yaw_std > args.max_yaw_std:
        raise RuntimeError(
            f"refusing to save uncertain pose: position_std={position_std:.3f}, yaw_std={yaw_std:.3f}"
        )
    pose = message.pose.pose
    yaw = math.atan2(
        2.0 * pose.orientation.w * pose.orientation.z,
        1.0 - 2.0 * pose.orientation.z * pose.orientation.z,
    )
    print("AMCL_SNAPSHOT " + json.dumps({
        "x": pose.position.x,
        "y": pose.position.y,
        "yaw": yaw,
        "position_std": position_std,
        "yaw_std": yaw_std,
        "saved_at": time.time(),
    }, sort_keys=True))


def build_parser():
    parser = argparse.ArgumentParser()
    subparsers = parser.add_subparsers(dest="command", required=True)

    preflight_parser = subparsers.add_parser("preflight")
    preflight_parser.add_argument("--timeout", type=float, default=10.0)
    preflight_parser.add_argument("--min-ranges", type=int, default=20)
    preflight_parser.set_defaults(handler=preflight)

    publish_parser = subparsers.add_parser("publish")
    publish_parser.add_argument("x", type=float)
    publish_parser.add_argument("y", type=float)
    publish_parser.add_argument("yaw", type=float)
    publish_parser.add_argument("--xy-std", type=float, default=0.35)
    publish_parser.add_argument("--yaw-std", type=float, default=0.35)
    publish_parser.add_argument("--timeout", type=float, default=8.0)
    publish_parser.set_defaults(handler=publish_initial_pose)

    spin_parser = subparsers.add_parser("spin")
    spin_parser.add_argument("--duration", type=float, default=12.0)
    spin_parser.add_argument("--angular", type=float, default=0.65)
    spin_parser.set_defaults(handler=spin_robot)

    check_parser = subparsers.add_parser("check")
    check_parser.add_argument("--timeout", type=float, default=12.0)
    check_parser.add_argument("--max-position-std", type=float, default=0.70)
    check_parser.add_argument("--max-yaw-std", type=float, default=0.55)
    check_parser.add_argument("--samples", type=int, default=3)
    check_parser.set_defaults(handler=check_convergence)

    snapshot_parser = subparsers.add_parser("snapshot")
    snapshot_parser.add_argument("--timeout", type=float, default=3.0)
    snapshot_parser.add_argument("--max-position-std", type=float, default=0.70)
    snapshot_parser.add_argument("--max-yaw-std", type=float, default=0.55)
    snapshot_parser.set_defaults(handler=snapshot)
    return parser


def main():
    args = build_parser().parse_args()
    rospy.init_node(f"ai_amcl_{args.command}", anonymous=True, disable_signals=True)
    try:
        args.handler(args)
    except Exception as exc:
        print(f"AMCL_{args.command.upper()}_ERROR {exc}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
