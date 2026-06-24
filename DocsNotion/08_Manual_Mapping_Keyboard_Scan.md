---
Page: Manual Mapping Keyboard Scan
Database: PFE Robot AI Documentation
Area: Mapping
Status: Ready
Tags: keyboard-scan, manual-mapping, gmapping
Related Files: keyboard_scan.sh, auto_scan.sh
---

# Manual Mapping Keyboard Scan

`keyboard_scan.sh` is the manual fallback for mapping. It is useful when automatic exploration is blocked, too slow, or unsafe in a room.

## Basic Command

```bash
./keyboard_scan.sh testsalle
```

## Purpose

Manual mapping lets the operator drive the robot slowly while gmapping builds the map. It is often the most reliable way to create a first high-quality map in a small room.

## Typical Use

1. Place robot in the room.
2. Start `keyboard_scan.sh`.
3. Drive slowly.
4. Rotate slowly at important viewpoints.
5. Cover doorways and corners.
6. Stop and save the map.

## Why Keep This Script

Automatic scan depends on:

- live lidar
- working gmapping
- valid TF
- move_base action server
- usable frontier detection
- enough free space for safe goals

Manual scan only needs:

- lidar
- robot base movement
- gmapping
- keyboard control

## Map Quality Rules

- Move slowly.
- Rotate slowly.
- Avoid hitting walls or furniture.
- Do not start with the robot touching an obstacle.
- Let gmapping settle before saving.
- Re-scan important walls if the map looks distorted.

## Output

The map is saved to:

```text
/root/yahboomcar_ws/src/yahboomcar_nav/maps/MAP_NAME.yaml
/root/yahboomcar_ws/src/yahboomcar_nav/maps/MAP_NAME.pgm
```

## Images To Add In Notion

- Screenshot of keyboard controls.
- Screenshot of final map from manual mapping.

