"""Quick diagnostic script — prints all class IDs that RF-DETR detects."""
import os
import sys

import cv2
import numpy as np
from rfdetr import RFDETRLarge

# Add project root so config.py is importable when run from tools/
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
from config import RTSP_URLS

print("Loading model...")
model = RFDETRLarge()
print(f"class_names[:10] = {model.class_names[:10]}\n")

print("Connecting to camera...")
os.environ["OPENCV_FFMPEG_CAPTURE_OPTIONS"] = "rtsp_transport;tcp"
cap = None
for url in RTSP_URLS:
    cap = cv2.VideoCapture(url, cv2.CAP_FFMPEG)
    if cap.isOpened():
        print(f"Connected: {url}\n")
        break
    cap.release()
    cap = None

if cap is None:
    print("ERROR: Could not connect to any camera URL.")
    sys.exit(1)

for i in range(5):
    ret, frame = cap.read()
    if not ret:
        print(f"Frame {i}: no data")
        continue
    rgb = cv2.cvtColor(frame, cv2.COLOR_BGR2RGB)
    det = model.predict(rgb, threshold=0.15)
    print(f"--- Frame {i} {frame.shape} | {len(det)} detections ---")
    if len(det) > 0:
        for cls_id in sorted(set(det.class_id.tolist())):
            mask  = det.class_id == cls_id
            name  = model.class_names[cls_id] if cls_id < len(model.class_names) else f"?{cls_id}"
            confs = det.confidence[mask]
            print(f"  [{cls_id}] {name}: {mask.sum()} dets  conf=[{confs.min():.3f},{confs.max():.3f}]")
    else:
        print("  (no detections)")

cap.release()
print("\nDone.")
