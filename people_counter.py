"""
people_counter.py
=================
Real-time people counting using RF-DETR + ByteTrack + supervision LineZone.
Serves a live annotated video stream over HTTP (default port 7004).

Usage:
    python people_counter.py
    Then open http://localhost:7004 in a browser.
"""

import json
import os
import threading
import time

import cv2
import numpy as np
import supervision as sv
import torch
from flask import Flask, Response
from rfdetr import RFDETRLarge

from config import (
    CONFIDENCE_THRESHOLD,
    DB_PATH,
    FRAME_RATE,
    LINE_CONFIG_PATH,
    LOST_TRACK_BUFFER,
    MAX_FAILURES_BEFORE_RECONNECT,
    MINIMUM_MATCHING_THRESHOLD,
    RTSP_URLS,
    STREAM_PORT,
    TRACK_ACTIVATION_THRESHOLD,
)
from dual_line_counter import SingleLineCounter
from event_engine import EventEngine

app = Flask(__name__)

# ─── COLOURS ─────────────────────────────────────────────────────────────────

COLOR_HEADER = (50, 50, 50)
COLOR_IN     = (0, 255, 0)
COLOR_OUT    = (0, 0, 255)

# ─── THREADED FRAME GRABBER ──────────────────────────────────────────────────


class RTSPFrameGrabber:
    """Thread-safe RTSP frame grabber with automatic graceful shutdown."""

    def __init__(self, source: str):
        self.source = source
        self.cap = None
        self.frame = None
        self.ret = False
        self.lock = threading.Lock()
        self.stopped = False
        self.frame_count = 0

    def start(self) -> bool:
        os.environ["OPENCV_FFMPEG_CAPTURE_OPTIONS"] = "rtsp_transport;tcp"
        self.cap = cv2.VideoCapture(self.source, cv2.CAP_FFMPEG)
        if not self.cap.isOpened():
            return False
        self.ret, self.frame = self.cap.read()
        if not self.ret:
            return False
        threading.Thread(target=self._update, daemon=True).start()
        return True

    def _update(self):
        """Background reader — exits gracefully on shutdown or camera drop."""
        while not self.stopped:
            try:
                ret, frame = self.cap.read()
                with self.lock:
                    self.ret = ret
                    self.frame = frame
                    self.frame_count += 1
                if not ret:
                    self.stopped = True
                    break
            except Exception:
                # OpenCV C++ exception during shutdown — exit cleanly
                self.stopped = True
                break

    def read(self):
        with self.lock:
            if self.frame is None:
                return False, None
            return self.ret, self.frame.copy()

    def stop(self):
        self.stopped = True
        time.sleep(0.1)
        try:
            if self.cap is not None:
                self.cap.release()
        except Exception:
            pass


# ─── ANNOTATORS (created once at module level, not per-frame) ────────────────

_box_annotator = sv.BoxAnnotator(
    color=sv.ColorPalette.from_hex(["#00BFFF"]),
    thickness=2,
)
_label_annotator = sv.LabelAnnotator(
    color=sv.ColorPalette.from_hex(["#00BFFF"]),
    text_color=sv.Color.WHITE,
    text_scale=0.4,
    text_thickness=1,
    text_padding=3,
)


# ─── DISPLAY HELPERS ─────────────────────────────────────────────────────────


def draw_counter_overlay(frame, in_count: int, out_count: int, fps: float, tracker_count: int):
    """Draw the semi-transparent stats overlay on the frame."""
    h, w = frame.shape[:2]
    overlay = frame.copy()
    cv2.rectangle(overlay, (0, 0), (w, 90), COLOR_HEADER, -1)
    cv2.addWeighted(overlay, 0.7, frame, 0.3, 0, frame)

    cv2.putText(frame, "PEOPLE COUNTER", (15, 28), cv2.FONT_HERSHEY_SIMPLEX, 0.7, (0, 255, 255), 2)

    # Show occupancy (IN - OUT) rather than meaningless combined total
    inside = max(0, in_count - out_count)
    cv2.putText(frame, f"IN: {in_count}",      (15, 60),  cv2.FONT_HERSHEY_SIMPLEX, 0.8, COLOR_IN,  2)
    cv2.putText(frame, f"OUT: {out_count}",    (200, 60), cv2.FONT_HERSHEY_SIMPLEX, 0.8, COLOR_OUT, 2)
    cv2.putText(frame, f"INSIDE: {inside}",    (400, 60), cv2.FONT_HERSHEY_SIMPLEX, 0.8, (0, 255, 255), 2)
    cv2.putText(frame, f"Tracking: {tracker_count}", (15, 82), cv2.FONT_HERSHEY_SIMPLEX, 0.5, (200, 200, 200), 1)

    fps_text = f"FPS: {fps:.1f}"
    text_size = cv2.getTextSize(fps_text, cv2.FONT_HERSHEY_SIMPLEX, 0.6, 2)[0]
    cv2.putText(frame, fps_text, (w - text_size[0] - 15, 28), cv2.FONT_HERSHEY_SIMPLEX, 0.6, (0, 255, 0), 2)
    return frame


def draw_tracking_annotations(frame, detections):
    """Draw bounding boxes and track-ID labels on the frame."""
    if detections is None or len(detections) == 0:
        return frame
    labels = []
    if detections.tracker_id is not None:
        for tid, cls_id, conf in zip(detections.tracker_id, detections.class_id, detections.confidence):
            labels.append(f"#{tid} {conf:.2f}")
    else:
        for cls_id, conf in zip(detections.class_id, detections.confidence):
            labels.append(f"cls:{cls_id} {conf:.2f}")
    frame = _box_annotator.annotate(scene=frame, detections=detections)
    frame = _label_annotator.annotate(scene=frame, detections=detections, labels=labels)
    return frame


def draw_count_line(frame, line):
    """Draw the counting line on the frame."""
    cv2.line(frame, (int(line[0][0]), int(line[0][1])), (int(line[1][0]), int(line[1][1])), (0, 255, 255), 3)
    cv2.putText(frame, "COUNTING LINE", (10, int(line[0][1]) - 10), cv2.FONT_HERSHEY_SIMPLEX, 0.6, (0, 255, 255), 2)
    return frame


# ─── INFERENCE GENERATOR ─────────────────────────────────────────────────────


def generate_frames():
    """Main inference + tracking generator — yields MJPEG frames for Flask."""
    print("\n[1/3] Loading RF-DETR Large model...")
    try:
        model = RFDETRLarge()
        if hasattr(model, "inference"):
            model.inference(dtype=torch.float16)
            print("      Model optimised for FP16 inference.")
    except Exception as e:
        print(f"      ERROR loading model: {e}")
        return

    print("\n[2/3] Connecting to camera...")
    grabber = None
    for url in RTSP_URLS:
        grabber = RTSPFrameGrabber(url)
        if grabber.start():
            print(f"      Connected: {url}")
            break
        grabber.stop()
        grabber = None

    if grabber is None:
        print("      ERROR: Could not connect to any camera URL.")
        return

    ret, first_frame = grabber.read()
    if not ret:
        print("      ERROR: Could not read first frame.")
        grabber.stop()
        return

    h, w = first_frame.shape[:2]

    # Default counting line — middle of frame, Right→Left direction
    base_y = h // 2
    line_start, line_end = (w, base_y), (0, base_y)

    if os.path.exists(LINE_CONFIG_PATH):
        try:
            with open(LINE_CONFIG_PATH, "r") as f:
                cfg = json.load(f)
                line_start = (cfg["start"]["x"], cfg["start"]["y"])
                line_end   = (cfg["end"]["x"],   cfg["end"]["y"])
            print(f"      Line config loaded: {line_start} → {line_end}")
        except Exception as e:
            print(f"      WARN: Could not read line_config.json ({e}). Using default.")

    print("\n[3/3] Starting tracking loop...")

    tracker = sv.ByteTrack(
        track_activation_threshold=TRACK_ACTIVATION_THRESHOLD,
        lost_track_buffer=LOST_TRACK_BUFFER,
        minimum_matching_threshold=MINIMUM_MATCHING_THRESHOLD,
        frame_rate=FRAME_RATE,
    )

    counter = SingleLineCounter(
        line_start,
        line_end,
        dead_zone=8,
        cooldown_frames=30,
        timeout_seconds=5.0,
        db_path=DB_PATH,
    )
    print(f"      Counting line: {line_start} → {line_end} | Frame: {w}x{h}")

    # ── Supplemental count engine ──────────────────────────────────────────────
    engine = EventEngine()
    engine.start()
    prev_real_in = counter.entered_count

    fps             = 0.0
    fps_start_time  = time.time()
    fps_frame_count = 0
    debug_frame_count       = 0
    consecutive_failures    = 0

    try:
        while True:
            ret, frame = grabber.read()

            if not ret or frame is None:
                consecutive_failures += 1
                if consecutive_failures >= MAX_FAILURES_BEFORE_RECONNECT:
                    print(f"[WARN] {consecutive_failures} consecutive read failures — reconnecting...")
                    grabber.stop()
                    time.sleep(2)
                    grabber = None
                    for url in RTSP_URLS:
                        grabber = RTSPFrameGrabber(url)
                        if grabber.start():
                            print(f"[INFO] Reconnected: {url}")
                            break
                        grabber.stop()
                        grabber = None
                    if grabber is None:
                        print("[ERROR] Reconnection failed. Exiting stream.")
                        return
                    consecutive_failures = 0
                else:
                    time.sleep(0.1)
                continue

            consecutive_failures = 0

            # ── Inference ────────────────────────────────────────────────────
            rgb_frame  = cv2.cvtColor(frame, cv2.COLOR_BGR2RGB)
            detections = model.predict(rgb_frame, threshold=CONFIDENCE_THRESHOLD)

            # Overhead cameras frequently misclassify heads/shoulders as bikes (1)
            # or motorcycles (3); include them to avoid dropping real people.
            person_mask = np.isin(detections.class_id, [0, 1, 3])
            detections  = detections[person_mask]

            # Normalise all accepted detections to Class 0 (person)
            if len(detections) > 0:
                detections.class_id[:] = 0

            # ── Tracking ─────────────────────────────────────────────────────
            detections = tracker.update_with_detections(detections)

            # ── Line crossing state machine ───────────────────────────────────
            counter.update(detections)

            # ── Notify supplemental engine on new real crossings ──────────────
            if counter.entered_count > prev_real_in:
                for _ in range(counter.entered_count - prev_real_in):
                    engine.notify_real_in(counter.entered_count)
                prev_real_in = counter.entered_count

            # ── Debug logging (every 60 frames) ──────────────────────────────
            debug_frame_count += 1
            if debug_frame_count % 60 == 0:
                ec = engine.get_extra_counts()
                print(
                    f"[DEBUG] Frame {debug_frame_count}: {len(detections)} tracked | "
                    f"IN={counter.entered_count} OUT={counter.exited_count} | "
                    f"active_tracks={len(counter.tracks)}"
                )
                counter.log_status(len(counter.tracks))

            # ── FPS counter ───────────────────────────────────────────────────
            fps_frame_count += 1
            elapsed = time.time() - fps_start_time
            if elapsed >= 1.0:
                fps             = fps_frame_count / elapsed
                fps_frame_count = 0
                fps_start_time  = time.time()

            # ── Build display frame ───────────────────────────────────────────
            ec           = engine.get_extra_counts()
            display_in   = counter.entered_count + ec["extra_in"]
            display_out  = counter.exited_count  + ec["extra_out"]

            annotated = frame.copy()
            annotated = draw_tracking_annotations(annotated, detections)
            annotated = draw_count_line(annotated, counter.line)
            annotated = draw_counter_overlay(annotated, display_in, display_out, fps, len(detections))

            # ── Encode MJPEG ──────────────────────────────────────────────────
            ok, buffer = cv2.imencode(".jpg", annotated)
            if not ok:
                continue

            yield (
                b"--frame\r\n"
                b"Content-Type: image/jpeg\r\n\r\n"
                + buffer.tobytes()
                + b"\r\n"
            )

    finally:
        print("[INFO] Client disconnected — releasing camera.")
        if grabber is not None:
            grabber.stop()


# ─── FLASK ROUTES ────────────────────────────────────────────────────────────


@app.route("/")
def index():
    return f"""<!DOCTYPE html>
<html lang="en">
<head>
    <meta charset="UTF-8">
    <title>People Counter — Live Feed</title>
    <style>
        body {{
            background: #000;
            margin: 0;
            height: 100vh;
            display: flex;
            justify-content: center;
            align-items: center;
            overflow: hidden;
        }}
        img {{
            width: 100vw;
            height: 100vh;
            object-fit: contain;
        }}
    </style>
</head>
<body>
    <img src="/video_feed" alt="Live camera feed" />
</body>
</html>"""


@app.route("/video_feed")
def video_feed():
    return Response(generate_frames(), mimetype="multipart/x-mixed-replace; boundary=frame")


# ─── ENTRY POINT ─────────────────────────────────────────────────────────────

if __name__ == "__main__":
    print(f"Starting People Counter stream server on http://0.0.0.0:{STREAM_PORT}")
    print(f"Open the analytics dashboard at http://0.0.0.0:{ANALYTICS_PORT} (run analytics_server.py)")
    app.run(host="0.0.0.0", port=STREAM_PORT, threaded=True)
