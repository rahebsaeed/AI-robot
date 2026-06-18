import cv2
import time
from ultralytics import YOLO

MODEL_PATH = "yolo11n.pt"
CAMERA_INDEX = 0
OUTPUT_IMAGE = "/tmp/yolo_test_result.jpg"

print("[YOLO TEST] Loading YOLO11n...")
model = YOLO(MODEL_PATH)
print("[YOLO TEST] Model loaded.")

cap = cv2.VideoCapture(CAMERA_INDEX)

if not cap.isOpened():
    print("[CAMERA ERROR] Camera not opened.")
    exit(1)

print("[CAMERA] Camera opened.")
print("[TEST] Capturing frames. No GUI window will be opened.")

last_objects = []

for i in range(20):
    ret, frame = cap.read()

    if not ret or frame is None:
        print(f"[FRAME {i}] No frame.")
        time.sleep(0.2)
        continue

    results = model(frame, imgsz=320, conf=0.25, verbose=False)

    objects = []

    for r in results:
        for box in r.boxes:
            cls_id = int(box.cls[0])
            conf = float(box.conf[0])
            name = model.names[cls_id]

            x1, y1, x2, y2 = box.xyxy[0].tolist()
            cx = (x1 + x2) / 2
            width = frame.shape[1]

            if cx < width / 3:
                position = "left"
            elif cx > 2 * width / 3:
                position = "right"
            else:
                position = "center"

            objects.append({
                "name": name,
                "confidence": round(conf, 2),
                "position": position
            })

            cv2.rectangle(
                frame,
                (int(x1), int(y1)),
                (int(x2), int(y2)),
                (0, 255, 0),
                2
            )

            cv2.putText(
                frame,
                f"{name} {conf:.2f} {position}",
                (int(x1), max(20, int(y1) - 5)),
                cv2.FONT_HERSHEY_SIMPLEX,
                0.5,
                (0, 255, 0),
                2
            )

    last_objects = objects

    print(f"[FRAME {i}] Objects: {objects}")

    cv2.imwrite(OUTPUT_IMAGE, frame)

    time.sleep(0.3)

cap.release()

print("")
print("[DONE] YOLO test finished.")
print(f"[IMAGE SAVED] {OUTPUT_IMAGE}")
print(f"[LAST OBJECTS] {last_objects}")
