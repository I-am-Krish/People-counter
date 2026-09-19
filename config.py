"""
config.py
=========
Central configuration for the People Counter system.
Edit this file to match your deployment environment.
"""

import os

# ─── CAMERA ──────────────────────────────────────────────────────────────────

# RTSP stream URLs to attempt (tried in order until one connects).
# Format: rtsp://<user>:<password>@<ip>:<port>/<path>
# You can also set a single URL via the environment variable RTSP_URL.
RTSP_URLS: list[str] = [
    os.environ.get("RTSP_URL", ""),  # Env-var override (recommended for production)
    "rtsp://admin:123456@192.168.1.35:554/0",
    "rtsp://admin:123456@192.168.1.35:554/stream1",
    "rtsp://admin:123456@192.168.1.35:554/1",
    "rtsp://admin:123456@192.168.1.35:554/Streaming/Channels/101",
    "rtsp://admin:123456@192.168.1.35:554/cam/realmonitor?channel=1&subtype=0",
]
# Remove empty strings (e.g., if env var is not set)
RTSP_URLS = [u for u in RTSP_URLS if u]

# ─── MODEL & TRACKER ─────────────────────────────────────────────────────────

CONFIDENCE_THRESHOLD: float = 0.35
TRACK_ACTIVATION_THRESHOLD: float = 0.25
LOST_TRACK_BUFFER: int = 90
MINIMUM_MATCHING_THRESHOLD: float = 0.8
FRAME_RATE: int = 15

# ─── SERVER PORTS ────────────────────────────────────────────────────────────

# Port for the live video-stream Flask server
STREAM_PORT: int = int(os.environ.get("STREAM_PORT", 7004))

# Port for the analytics dashboard Flask server
ANALYTICS_PORT: int = int(os.environ.get("ANALYTICS_PORT", 7003))

# ─── DATABASE ────────────────────────────────────────────────────────────────

DB_PATH: str = os.path.join(os.path.dirname(__file__), "analytics.db")

# ─── LINE CONFIG ─────────────────────────────────────────────────────────────

LINE_CONFIG_PATH: str = os.path.join(os.path.dirname(__file__), "line_config.json")

# ─── RECONNECTION ────────────────────────────────────────────────────────────

# Number of consecutive failed frame reads before attempting camera reconnect
MAX_FAILURES_BEFORE_RECONNECT: int = 50
