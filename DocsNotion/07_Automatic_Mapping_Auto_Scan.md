---
Page: Automatic Mapping Auto Scan
Database: PFE Robot AI Documentation
Area: Mapping
Status: Ready
Tags: auto-scan, gmapping, frontier, move-base
Related Files: auto_scan.sh, tools/professional_explorer.py, docs/README.md
---

# Automatic Mapping Auto Scan

`auto_scan.sh` creates or replaces a map using gmapping and a professional frontier explorer.

## Basic Command

```bash
./auto_scan.sh testsalle
```

Map names may contain only letters, numbers, underscore, and hyphen.

## Main Behavior

- Starts Docker and ROS.
- Restarts robot hardware service.
- Starts Yahboom bringup.
- Starts gmapping.
- Starts move_base.
- Validates `/scan`, `/map`, `/tf`, and move_base.
- Runs `tools/professional_explorer.py`.
- Saves the map with verified replacement.
- Stops mapping and navigation nodes.

## Existing Map Behavior

By default, if the same map name already exists:

1. The old `.yaml`, `.pgm`, and exploration memory are backed up.
2. The old files are removed.
3. A fresh scan creates new map files with the same name.

To keep old files:

```bash
AUTO_SCAN_KEEP_EXISTING=1 ./auto_scan.sh testsalle
```

To skip completed maps:

```bash
AUTO_SCAN_SKIP_COMPLETED=1 ./auto_scan.sh testsalle
```

## Explorer Logic

The explorer:

- detects unknown/free frontiers
- scores goals by information gain and path cost
- uses move_base if available
- can fallback to internal A* when allowed
- remembers visited, failed, and traversed positions
- uses lidar guarded recovery probes
- confirms completion before saving

## Safety Parameters

| Variable | Default | Purpose |
| --- | --- | --- |
| `AUTO_SCAN_REQUIRE_MOVE_BASE` | `1` | Stop if move_base is unavailable. Avoids slow fallback unless explicitly allowed. |
| `AUTO_SCAN_USE_MOVE_BASE` | `1` | Prefer move_base when available. |
| `AUTO_SCAN_MAX_LINEAR_SPEED` | `0.35` | Maximum move_base linear speed. |
| `AUTO_SCAN_MAX_ANGULAR_SPEED` | `1.00` | Maximum move_base angular speed. |
| `AUTO_SCAN_OBSTACLE_STOP_DISTANCE` | `0.55` | Stop distance in internal A*. |
| `AUTO_SCAN_OBSTACLE_SLOW_DISTANCE` | `0.90` | Slow distance in internal A*. |
| `AUTO_SCAN_COSTMAP_INFLATION_RADIUS` | `0.50` | Costmap obstacle inflation radius. |
| `AUTO_SCAN_RECOVERY_MIN_FRONT_CLEARANCE` | `0.75` | Front clearance required before recovery drive. |

## Completion Parameters

| Variable | Default | Purpose |
| --- | --- | --- |
| `AUTO_SCAN_COMPLETION_CONFIRMATIONS` | `3` | No-frontier checks before completion. |
| `AUTO_SCAN_MIN_COMPLETION_COVERAGE` | `0.40` | Minimum coverage evidence. |
| `AUTO_SCAN_MIN_COMPLETION_GOALS` | `1` | Minimum successful goals. |
| `AUTO_SCAN_MAX_RUNTIME` | `180` | Runtime cap for weak or blocked exploration. |
| `AUTO_SCAN_MAX_GOALS` | `10` | Goal cap, zero means unlimited. |

## Logs

| Log | Purpose |
| --- | --- |
| `/tmp/bringup_auto_scan.log` | Hardware/lidar/camera bringup. |
| `/tmp/gmapping_auto_scan.log` | Gmapping. |
| `/tmp/move_base_auto_scan.log` | move_base startup and errors. |

## Images To Add In Notion

- Terminal screenshot of successful auto scan.
- RViz screenshot of gmapping in progress.
- Final saved map image.

