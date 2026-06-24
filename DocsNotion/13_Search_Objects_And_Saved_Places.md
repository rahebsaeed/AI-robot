---
Page: Search Objects And Saved Places
Database: PFE Robot AI Documentation
Area: AI
Status: Ready
Tags: search, yolo, places, waypoints
Related Files: core/search_tasks.py, core/places_memory.py, main.py, core/robot.py
---

# Search Objects And Saved Places

The system supports two map-based behaviors: search for visual objects and navigate to saved places.

## Search Requests

Detected phrases include:

- `search for`
- `look for`
- `find`
- `where is`
- `where are`
- `locate`
- `go find`
- `go to`

The parser avoids treating `where are you` as object search.

## YOLO Target Resolution

`core/search_tasks.py` maps user names to YOLO classes.

Examples:

| User says | YOLO class |
| --- | --- |
| phone | cell phone |
| sofa | couch |
| table | dining table |
| human | person |
| bag | backpack |
| screen | tv |

## Search Flow

1. Validate target.
2. Check if target is a saved place.
3. If saved place exists, navigate directly to that place.
4. If visual object, check current camera first.
5. Clear costmaps.
6. Generate free-space waypoints from the map.
7. Navigate to waypoints by distance from current AMCL pose.
8. Check camera live while moving.
9. Stop if target is detected.
10. Skip stuck waypoints.
11. Report found, not found, or cancelled.

## Saved Places

Saved places are stored in:

```text
config/places.json
```

Commands:

| Command | Result |
| --- | --- |
| `save this place as door` | Saves current AMCL pose as `door`. |
| `go to door` | Navigates to saved place `door`. |
| `delete place door` | Removes saved place. |
| `rename place door to entrance` | Renames saved place. |
| `how many places do you know` | Lists count and names. |
| `what is this place` | Compares current AMCL pose to saved places. |

## Waypoint Generation

`Robot.get_search_waypoints()`:

- reads map YAML and PGM
- applies clearance from walls
- selects free cells
- converts image pixels to map coordinates
- limits count with `AI_SEARCH_MAX_POINTS`

## Search Tuning

| Variable | Default | Meaning |
| --- | --- | --- |
| `AI_SEARCH_NAV_TIMEOUT` | `14.0` | Time per waypoint. |
| `AI_SEARCH_STUCK_TIMEOUT` | `8.0` | No-progress timeout. |
| `AI_SEARCH_START_GRACE_SECONDS` | `5.0` | Grace period before stuck detection. |
| `AI_SEARCH_MAX_STUCK_GOALS` | `4` | Max consecutive stuck goals. |
| `AI_SEARCH_SPACING_M` | `1.00` | Waypoint spacing. |
| `AI_SEARCH_CLEARANCE_M` | `0.65` | Clearance from walls. |
| `AI_SEARCH_MAX_POINTS` | `90` | Waypoint limit. |

## Images To Add In Notion

- Map screenshot with generated search waypoints.
- Log screenshot for a successful object search.
- Screenshot of saved places JSON.

