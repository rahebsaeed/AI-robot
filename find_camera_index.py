import cv2
import os
import time

OUTPUT_DIR = "/tmp/camera_test_images"
os.makedirs(OUTPUT_DIR, exist_ok=True)

print("Scanning camera indexes 0 to 10...")
print("Images will be saved in:", OUTPUT_DIR)
print("")

for index in range(0, 11):
    print(f"Testing camera index {index}...")

    cap = cv2.VideoCapture(index)

    if not cap.isOpened():
        print(f"  Camera {index}: not opened")
        cap.release()
        continue

    # Give camera time to warm up
    time.sleep(0.5)

    ret, frame = cap.read()

    if not ret or frame is None:
        print(f"  Camera {index}: opened but no frame")
        cap.release()
        continue

    h, w = frame.shape[:2]

    filename = os.path.join(OUTPUT_DIR, f"camera_{index}.jpg")
    cv2.imwrite(filename, frame)

    print(f"  Camera {index}: OK | size={w}x{h} | saved={filename}")

    cap.release()

print("")
print("Done.")
print("Open the images from /tmp/camera_test_images and find the head Astra camera.")
