---
Page: Testing Validation Quality
Database: PFE Robot AI Documentation
Area: Testing
Status: Ready
Tags: tests, validation, quality
Related Files: tests/, tools/mock_robot_server.py, android-app/
---

# Testing Validation Quality

Testing is split into local PC tests and robot hardware validation.

## Local Unit Tests

Run from project root:

```bash
python3 -m unittest tests.test_ai_response_logic tests.test_professional_explorer tests.test_mobile_components
```

Current coverage areas:

| Test File | Scope |
| --- | --- |
| `tests/test_ai_response_logic.py` | AI command routing and response behavior. |
| `tests/test_professional_explorer.py` | Frontier exploration logic. |
| `tests/test_mobile_components.py` | Mobile gateway/control components. |

## Shell Syntax Checks

```bash
bash -n start_navigation.sh auto_scan.sh keyboard_scan.sh tools/diagnose_rosmaster_default_app.sh
```

## Python Compile Checks

```bash
python3 -m py_compile main.py core/*.py tools/*.py
```

## Android Tests

```bash
cd android-app
./gradlew test
```

Important Android tests:

- map projection
- map viewport zoom/pan
- speech engine selection

## PC Simulator

Use `tools/mock_robot_server.py` to test the Android app without the robot.

```bash
python3 tools/mock_robot_server.py
```

Then connect Android app to the PC IP and port.

## Robot Validation Checklist

| Test | Expected Result |
| --- | --- |
| Start navigation | All topics/services start and AMCL converges. |
| Emergency stop | Robot stops immediately from Android and face UI. |
| Manual control | Press-hold moves, release stops. |
| Search bottle | Robot navigates and checks camera. |
| Save place | `config/places.json` updates. |
| Go to place | move_base goal is sent and robot arrives. |
| Auto scan | Map is saved with new checksum. |
| CTRL+C | Costmaps cleared, static map not overwritten. |

## Quality Rules

- Do not add movement paths without lidar safety.
- Do not let the LLM decide physical facts that code can know.
- Do not rely on stale AMCL pose by default.
- Keep robot microphone muted unless needed.
- Keep stop paths outside the normal command queue when possible.

## Images To Add In Notion

- Screenshot of passing test output.
- Video or screenshots of hardware validation.

