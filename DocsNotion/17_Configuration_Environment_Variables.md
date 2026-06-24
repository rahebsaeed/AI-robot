---
Page: Configuration Environment Variables
Database: PFE Robot AI Documentation
Area: Configuration
Status: Ready
Tags: env, configuration, tuning
Related Files: start_navigation.sh, auto_scan.sh, main.py, core/perceptions.py, core/search_tasks.py
---

# Configuration Environment Variables

This page lists the most important runtime variables.

## AMCL

| Variable | Default | Use |
| --- | --- | --- |
| `AI_AMCL_ATTEMPTS` | `3` | Number of localization attempts. |
| `AI_AMCL_SPIN_SECONDS` | `12` | Fresh global localization spin duration. |
| `AI_AMCL_REFINEMENT_SPIN_SECONDS` | `5` | Follow-up refinement spin. |
| `AI_AMCL_SPIN_ANGULAR` | `0.65` | Spin angular speed. |
| `AI_AMCL_USE_SAVED_POSE` | `0` | Reuse saved pose only when explicitly enabled. |
| `AI_AMCL_SAVE_POSE` | `0` | Save pose for optional reuse. |
| `AI_AMCL_INITIAL_X/Y/YAW` | unset | Explicit known start pose. |

## Shutdown

| Variable | Default | Use |
| --- | --- | --- |
| `AI_CLEAR_COSTMAPS_ON_EXIT` | `1` | Clear costmaps on CTRL+C. |
| `AI_SAVE_MAP_ON_EXIT` | `0` | Save map on shutdown only when live SLAM is running. |
| `AI_SAVE_STATIC_MAP_ON_EXIT` | `0` | Force re-saving static map. Rarely needed. |

## Mobile Gateway

| Variable | Default | Use |
| --- | --- | --- |
| `AI_MOBILE_ENABLED` | `1` | Enable Android WebSocket. |
| `AI_MOBILE_HOST` | `0.0.0.0` | Bind address. |
| `AI_MOBILE_PORT` | `8765` | WebSocket port. |
| `AI_MOBILE_TOKEN` | empty | Pairing token. Empty means open LAN mode. |

## Microphone And STT

| Variable | Default | Use |
| --- | --- | --- |
| `AI_ROBOT_MIC_DEFAULT_MUTED` | `1` | Robot mic starts muted. |
| `AI_MIC_LISTEN_WHILE_MOVING` | `0` | Allow listening while moving. |
| `ROBOT_MIC_INDEX` | auto | Force microphone index. |
| `ROBOT_MIC_CHANNELS` | `1` | Microphone channels. |
| `AI_MIC_CALIBRATION_SECONDS` | `1.5` | Initial calibration. |
| `AI_MIC_RECALIBRATION_SECONDS` | `45` | Ambient recalibration interval. |
| `AI_MIC_DYNAMIC_RATIO` | `1.8` | Energy threshold ratio. |
| `AI_WHISPER_MODEL` | `auto` | Whisper model selection. |
| `AI_WHISPER_LANGUAGE` | `en` | Whisper language. |
| `AI_WHISPER_BEAM_SIZE` | `5` | Beam search size. |

## Vision

| Variable | Default | Use |
| --- | --- | --- |
| `AI_YOLO_MODEL` | `yolo11n.pt` | YOLO model path. |
| `AI_YOLO_CONF` | `0.25` | YOLO confidence threshold. |
| `AI_OPEN_VOCAB` | `0` | Enable open-vocabulary mode if model exists. |

## Search

| Variable | Default | Use |
| --- | --- | --- |
| `AI_SEARCH_NAV_TIMEOUT` | `14.0` | Time per waypoint. |
| `AI_SEARCH_STUCK_TIMEOUT` | `8.0` | No-progress timeout. |
| `AI_SEARCH_START_GRACE_SECONDS` | `5.0` | Startup grace before stuck detection. |
| `AI_SEARCH_MAX_STUCK_GOALS` | `4` | Max stuck goals before warning. |
| `AI_SEARCH_CONFIRM_READS` | `1` | Required detection confirmations. |
| `AI_SEARCH_SPACING_M` | `1.00` | Waypoint spacing. |
| `AI_SEARCH_CLEARANCE_M` | `0.65` | Wall clearance. |
| `AI_SEARCH_MAX_POINTS` | `90` | Waypoint limit. |

## Auto Scan

| Variable | Default | Use |
| --- | --- | --- |
| `AUTO_SCAN_REQUIRE_MOVE_BASE` | `1` | Fail if move_base unavailable. |
| `AUTO_SCAN_USE_MOVE_BASE` | `1` | Use move_base when available. |
| `AUTO_SCAN_MAX_LINEAR_SPEED` | `0.35` | Exploration speed. |
| `AUTO_SCAN_MAX_ANGULAR_SPEED` | `1.00` | Rotation speed. |
| `AUTO_SCAN_KEEP_EXISTING` | `0` | Keep old map files. |
| `AUTO_SCAN_SKIP_COMPLETED` | `0` | Skip completed maps. |
| `AUTO_SCAN_MAX_RUNTIME` | `180` | Runtime cap. |
| `AUTO_SCAN_MAX_GOALS` | `10` | Goal cap. |

## Images To Add In Notion

- Screenshot of `.env` or shell exports used during final demo.

