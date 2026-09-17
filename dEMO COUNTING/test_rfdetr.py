import cv2
import os
import supervision as sv
from rfdetr import RFDETRBase
import torch
print("Loading model...")
model = RFDETRBase()
print("Connecting to camera...")
os.environ['OPENCV_FFMPEG_CAPTURE_OPTIONS'] = 'rtsp_transport;tcp'
cap = cv2.VideoCapture("rtsp://admin:123456@192.168.1.35:554/stream1", cv2.CAP_FFMPEG)
ret, frame = cap.read()
if ret:
    print("Got frame!")
    rgb = cv2.cvtColor(frame, cv2.COLOR_BGR2RGB)
    dets = model.predict(rgb, threshold=0.10)
    print(f"Found {len(dets)} detections")
    if len(dets) > 0:
        print("Classes:", dets.class_id)
else:
    print("Failed to get frame")
