# People Counter — Real-Time AI Footfall Monitoring

<div align="center">

![Python](https://img.shields.io/badge/Python-3.10%2B-blue?logo=python&logoColor=white)
![PyTorch](https://img.shields.io/badge/PyTorch-GPU%20Accelerated-EE4C2C?logo=pytorch&logoColor=white)
![RF-DETR](https://img.shields.io/badge/RF--DETR-Object%20Detection-blueviolet)
![ByteTrack](https://img.shields.io/badge/ByteTrack-Multi--Object%20Tracking-orange)
![Flask](https://img.shields.io/badge/Flask-Web%20Dashboard-lightgrey?logo=flask)
![License](https://img.shields.io/badge/License-MIT-green)

**Production-deployed AI people counter that has tracked over 50,000+ people with 85%+ accuracy.**

[Live Feed](#-quick-start) · [Dashboard](#-analytics-dashboard) · [Configuration](#-configuration) · [Architecture](#-architecture)

</div>

---

## Overview

This system provides real-time, accurate people counting from RTSP camera streams using a cutting-edge transformer-based detector. It has been successfully deployed in high-density crowd environments — tracking over **50,000 people** with sustained **85%+ accuracy** even under challenging overhead camera angles and dense crowd conditions.

### Key Features

- **State-of-the-art Detection** — RF-DETR Large transformer model, GPU-accelerated with FP16 inference
- **Robust Tracking** — ByteTrack multi-object tracker with configurable hysteresis dead zone to eliminate false counts
- **Live Analytics Dashboard** — Beautiful dark-mode web dashboard with real-time charts and crowd capacity meter
- **Auto-Reconnect** — Automatically re-establishes RTSP connection if the camera feed drops
- **Persistent Counts** — SQLite event log preserves counts across restarts — no data loss on reboot
- **Network Accessible** — Both the live feed and dashboard are served over HTTP, accessible from any device on the network
- **One-Click Launch** — Single script starts both servers on Windows, Linux, and macOS

---

## Quick Start

### Prerequisites

- Python 3.10 or higher
- CUDA-capable GPU (recommended) or CPU
- An RTSP-capable IP camera

### 1. Clone the repository

```bash
git clone https://github.com/I-am-Krish/People-counter.git
cd People-counter
```

### 2. Configure your camera

Edit [`config.py`](config.py) and update the `RTSP_URLS` list with your camera's stream address:

```python
RTSP_URLS = [
    "rtsp://admin:your_password@192.168.1.X:554/stream1",
]
```

> **Tip:** You can also set the `RTSP_URL` environment variable instead of editing the file — safer for production deployments.

### 3. Launch

**Windows (double-click or run in CMD):**
```bat
launch.bat
```

**Linux / macOS:**
```bash
chmod +x launch.sh
./launch.sh
```

The launcher will automatically:
1. Create a Python virtual environment
2. Install all dependencies
3. Start both the video-stream server and analytics dashboard

### 4. Open in browser

| Service | URL |
|---|---|
| Live annotated feed | `http://localhost:7004` |
| Analytics dashboard | `http://localhost:7003` |

---

## Analytics Dashboard

The web dashboard provides a real-time overview of footfall:

- **Total IN / OUT counts** — updated every second
- **Current occupancy** — people currently inside the monitored area
- **Crowd capacity meter** — doughnut chart with safe / warning / overcrowded states
- **Hourly inflow chart** — bar chart of visitor distribution across the day

---

## Configuration

All settings are in [`config.py`](config.py):

| Setting | Default | Description |
|---|---|---|
| `RTSP_URLS` | — | Camera stream URLs (tried in order) |
| `CONFIDENCE_THRESHOLD` | `0.35` | Minimum detection confidence |
| `STREAM_PORT` | `7004` | Port for the live video feed server |
| `ANALYTICS_PORT` | `7003` | Port for the analytics dashboard server |
| `MAX_FAILURES_BEFORE_RECONNECT` | `50` | Consecutive frame failures before reconnect |

The counting line position is configured in [`line_config.json`](line_config.json):

```json
{
    "start": {"x": 704, "y": 415},
    "end":   {"x": 0,   "y": 415}
}
```

---

## Architecture

```
people_counter.py          ← Main process: RF-DETR inference + ByteTrack + Flask stream (port 7004)
│
├── dual_line_counter.py   ← SingleLineCounter state machine (hysteresis crossing detection)
│   └── analytics.db       ← SQLite event log (IN/OUT events + live_status)
│
├── event_engine.py        ← Background supplemental event engine (daemon thread)
│
analytics_server.py        ← Separate Flask process: dashboard + REST API (port 7003)
│
├── templates/
│   └── analytics.html     ← Dashboard UI (Chart.js + Inter font)
│
config.py                  ← Central configuration (ports, thresholds, RTSP URLs)
config.json                ← Runtime event-engine config (hot-reloaded every 30s)
line_config.json           ← Counting line pixel coordinates
```

### How Counting Works

1. Each video frame is passed through **RF-DETR Large** for object detection
2. Detections are filtered to people (class IDs 0, 1, 3) and normalised to class 0
3. **ByteTrack** assigns persistent track IDs across frames
4. **SingleLineCounter** watches each track's bounding-box centre relative to the counting line
5. A crossing is registered only when the centre moves from one side to the other AND is at least `dead_zone` pixels away from the line — preventing jitter false counts
6. After a crossing, the track enters a `cooldown_frames` period to prevent double-counting
7. Every IN/OUT event is persisted to SQLite with a timestamp — counts survive restarts

---

## Developer Tools

The `tools/` directory contains diagnostic scripts for development:

```bash
# Print all class IDs detected by RF-DETR on your camera
python tools/debug_classes.py
```

---

## Dependencies

| Package | Purpose |
|---|---|
| `rfdetr` | RF-DETR transformer object detector |
| `supervision` | Detection utilities + ByteTrack multi-object tracker |
| `opencv-python` | Video capture and image processing |
| `torch` | GPU-accelerated inference backend |
| `flask` | Lightweight web server for live feed and dashboard |
| `numpy` | Numerical operations |

Install all:
```bash
pip install -r requirements.txt
```
