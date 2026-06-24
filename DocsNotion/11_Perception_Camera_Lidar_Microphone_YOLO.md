---
Page: Perception Camera Lidar Microphone YOLO
Database: PFE Robot AI Documentation
Area: AI
Status: Ready
Tags: perception, camera, lidar, microphone, yolo, whisper
Related Files: core/perceptions.py, start_navigation.sh, yolo11n.pt
---

# Perception Camera Lidar Microphone YOLO

`core/perceptions.py` owns live sensor processing and speech I/O.

## Inputs

| Input | Source | Transport |
| --- | --- | --- |
| Lidar summary | ROS `/scan` bridge | UDP `5010` |
| Camera frame | ROS `/camera/rgb/image_raw` bridge | UDP `5030` |
| Robot microphone | USB microphone | SpeechRecognition and Whisper |

## Outputs

| Output | Method |
| --- | --- |
| Object detections | YOLO11 on latest camera frame. |
| Vision description | Human-readable object summary. |
| Lidar distance | Front, all, left, right, rear values. |
| Speech text | Whisper transcription. |
| Robot voice | `espeak-ng`. |

## Camera Flow

1. Docker bridge subscribes to `/camera/rgb/image_raw`.
2. Frame is resized to `320x240`.
3. JPEG is sent by UDP to port `5030`.
4. `Perceptions` decodes and stores latest frame.
5. YOLO runs on demand.
6. `see()` returns frame plus structured vision data.

## Lidar Flow

1. Docker bridge reads `/scan`.
2. It extracts valid ranges.
3. It sends front and all minimum distance to UDP `5010`.
4. `Perceptions` caches values for safety and speech context.

## Microphone Behavior

The robot microphone starts muted by default:

```bash
AI_ROBOT_MIC_DEFAULT_MUTED=1
```

Enable it from Android or face UI only when needed.

Reasons:

- avoid false commands during navigation
- avoid transcribing robot speech
- avoid motor noise and background voices

## Whisper Selection

Default:

```bash
AI_WHISPER_MODEL=auto
```

Auto mode prefers cached models only:

1. `turbo`
2. `small.en`
3. `base.en`
4. `tiny.en`

## YOLO

Default model:

```text
yolo11n.pt
```

Default confidence:

```bash
AI_YOLO_CONF=0.25
```

## Important Failure Modes

| Symptom | Likely Cause |
| --- | --- |
| `No camera frame yet` | RGB bridge not receiving `/camera/rgb/image_raw`. |
| Camera sees arm feed instead of head feed | `/dev/video*` device order changed. |
| False microphone commands | Robot mic unmuted, low threshold, or echo. |
| YOLO misses object | Low light, small object, wrong class, confidence too high. |
| Lidar unknown | `/scan` missing or UDP bridge stopped. |

## Images To Add In Notion

- Screenshot of camera frame with YOLO boxes.
- Screenshot of microphone list at startup.
- Screenshot of lidar values in logs.

