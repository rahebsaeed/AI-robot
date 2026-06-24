---
Page: Glossary
Database: PFE Robot AI Documentation
Area: Reference
Status: Ready
Tags: glossary, reference
Related Files: docs/README.md, core/, tools/
---

# Glossary

| Term | Meaning |
| --- | --- |
| AMCL | Adaptive Monte Carlo Localization. Estimates robot pose on a known map. |
| Astra camera | Depth/RGB camera used by Yahboom. RGB topic is `/camera/rgb/image_raw`. |
| Costmap | Grid used by move_base to represent obstacles and inflation. |
| Docker ROS zone | `yahboom_container`, where ROS1 nodes run. |
| Gmapping | ROS SLAM mapper used to build occupancy grid maps. |
| Host AI zone | Jetson host Python processes outside Docker. |
| Lidar | Laser scanner producing `/scan`. |
| MakerControl | Default Yahboom/Rosmaster phone app. |
| Map server | ROS node loading saved `.yaml` and `.pgm` map files. |
| move_base | ROS navigation action server and planner/controller framework. |
| Notion database row | One imported markdown file/page in `DocsNotion`. |
| Occupancy grid | Map image where pixels represent free, occupied, or unknown space. |
| Pose | Robot position and orientation, usually `x`, `y`, `yaw`. |
| ROS master | ROS1 coordinator, normally `http://ROBOT_IP:11311`. |
| ROS_IP | Environment variable telling ROS which IP this machine advertises. |
| RViz | ROS visualization tool for map, lidar, pose, TF, and plans. |
| TF | ROS transform tree between frames such as `map`, `odom`, `base_footprint`, `laser`. |
| UDP bridge | Small script moving data between ROS in Docker and host Python. |
| WebSocket | Persistent JSON connection between Android app and robot. |
| YOLO | Object detector used for camera search. |

## Important Frames

| Frame | Meaning |
| --- | --- |
| `map` | Global map coordinate frame. |
| `odom` | Odometry frame. |
| `base_footprint` | Robot base frame. |
| `laser` | Lidar frame. |

## Important Ports

| Port | Use |
| --- | --- |
| `5005` | Face UI receives status. |
| `5006` | Main AI receives face UI commands. |
| `5010` | Lidar UDP bridge. |
| `5020` | Movement command bridge. |
| `5030` | Camera frame bridge. |
| `5040` | AMCL pose bridge. |
| `8765` | Custom Android WebSocket. |
| `11311` | ROS master. |

## Images To Add In Notion

- Frame tree screenshot from RViz TF display.

