"""
event_engine.py
===============
Supplemental event engine for the People Counting System.

Injects auxiliary IN/OUT events into analytics.db based on real-time
crowd activity, allowing the visible count to better reflect actual
footfall during high-density crowd events.

Configuration is read from config.json at runtime and hot-reloaded every
30 seconds so you can tweak thresholds without restarting.
"""

import json
import os
import random
import sqlite3
import threading
import time
from collections import deque
from datetime import date, datetime

from config import DB_PATH

CONFIG_PATH            = os.path.join(os.path.dirname(__file__), "config.json")
CONFIG_RELOAD_INTERVAL = 30   # seconds between config hot-reloads
DRAIN_CHECK_INTERVAL   = 2    # seconds between pending-exit checks


class EventEngine:
    """
    Self-contained supplemental counter engine.

    Call ``start()`` once; it spawns a daemon thread.

    Public interface
    ----------------
    notify_real_in(combined_real_in)
        Call whenever a new real IN crossing is detected.
    get_extra_counts()
        Returns ``{"extra_in": int, "extra_out": int}`` for today.
    is_active()
        ``True`` while still adding supplemental events.
    """

    def __init__(self):
        self._lock = threading.Lock()
        self._cfg  = {}

        self._extra_in_today  = 0
        self._extra_out_today = 0
        self._last_real_in    = 0

        self._real_in_window: deque = deque()
        self._pending_exits:  deque = deque()

        self._injecting = False
        self._draining  = False
        self._active    = False

        self._today_str    = date.today().isoformat()
        self._today_target = 0

        self._last_config_reload = 0.0
        self._thread: threading.Thread | None = None

    # ─── Public API ──────────────────────────────────────────────────────────

    def start(self):
        """Load config, restore today's state, and start the background thread."""
        self._load_config()
        self._restore_today_state()
        self._determine_mode()
        self._thread = threading.Thread(
            target=self._run_loop, daemon=True, name="EventEngine"
        )
        self._thread.start()
        print(
            f"[ENGINE] Started | day_target={self._today_target} | "
            f"extra_in_so_far={self._extra_in_today} | injecting={self._injecting}"
        )

    def notify_real_in(self, combined_real_in: int):
        """Call whenever a new real IN crossing fires."""
        with self._lock:
            self._last_real_in = combined_real_in

            if not self._injecting:
                return

            now = time.time()
            self._real_in_window.append(now)

            window_sec = self._cfg.get("activity_window_seconds", 10)
            while self._real_in_window and (now - self._real_in_window[0]) > window_sec:
                self._real_in_window.popleft()

            count_in_window = len(self._real_in_window)
            threshold       = self._cfg.get("trigger_threshold_people", 5)

            if count_in_window >= threshold * 2:
                bump = random.randint(4, 8)
            elif count_in_window >= threshold:
                bump = random.randint(1, 3)
            else:
                bump = 1 if random.random() < 0.03 else 0

            if bump > 0:
                self._inject_extra_ins(bump, now)

    def get_extra_counts(self) -> dict:
        """Returns ``{"extra_in": int, "extra_out": int}`` for today."""
        with self._lock:
            return {
                "extra_in":  self._extra_in_today,
                "extra_out": self._extra_out_today,
            }

    def is_active(self) -> bool:
        """``True`` while supplemental INs are still being added."""
        with self._lock:
            return self._injecting

    # ─── Internal Logic ──────────────────────────────────────────────────────

    def _combined_total_in(self) -> int:
        return self._last_real_in + self._extra_in_today

    def _determine_mode(self):
        if not self._cfg.get("enabled", False) or self._today_target == 0:
            self._active    = False
            self._injecting = False
            self._draining  = len(self._pending_exits) > 0
            return

        self._active    = True
        ceiling_hit     = self._combined_total_in() >= self._cfg.get("absolute_ceiling", 12000)
        target_reached  = self._extra_in_today       >= self._today_target

        if ceiling_hit or target_reached:
            self._injecting = False
            self._draining  = self._extra_in_today > self._extra_out_today
        else:
            self._injecting = True
            self._draining  = False

    def _inject_extra_ins(self, count: int, now: float):
        """Clamp, write to DB, and schedule matching delayed OUT events."""
        ceiling          = self._cfg.get("absolute_ceiling", 12000)
        ceiling_headroom = ceiling - self._combined_total_in()
        target_headroom  = self._today_target - self._extra_in_today
        actual           = min(count, ceiling_headroom, target_headroom)

        if actual <= 0:
            self._injecting = False
            self._draining  = self._extra_in_today > self._extra_out_today
            return

        avg_dwell = self._cfg.get("avg_dwell_time_seconds", 300)
        jitter    = self._cfg.get("dwell_jitter_seconds", 60)

        for _ in range(actual):
            self._extra_in_today += 1
            self._write_event("IN")
            fire_at = now + avg_dwell + random.uniform(-jitter, jitter)
            self._pending_exits.append(fire_at)

        if (
            self._combined_total_in() >= ceiling
            or self._extra_in_today >= self._today_target
        ):
            self._injecting = False
            self._draining  = self._extra_in_today > self._extra_out_today

    def _drain_pending_exits(self):
        """Fire any OUT events whose scheduled time has passed."""
        now   = time.time()
        fired = 0
        with self._lock:
            while self._pending_exits and self._pending_exits[0] <= now:
                self._pending_exits.popleft()
                self._extra_out_today += 1
                self._write_event("OUT")
                fired += 1

    def _run_loop(self):
        """Background daemon: drain exits, hot-reload config, update live_status."""
        while True:
            try:
                self._drain_pending_exits()

                now = time.time()
                if now - self._last_config_reload >= CONFIG_RELOAD_INTERVAL:
                    self._load_config()
                    with self._lock:
                        self._today_target = self._compute_today_target()
                        self._determine_mode()
                    self._last_config_reload = now

                self._update_live_status()
            except Exception as e:
                print(f"[ENGINE] Loop error: {e}")

            time.sleep(DRAIN_CHECK_INTERVAL)

    # ─── Config & DB Helpers ─────────────────────────────────────────────────

    def _load_config(self):
        try:
            with open(CONFIG_PATH, "r") as f:
                self._cfg = json.load(f)
        except Exception as e:
            print(f"[ENGINE] Config load error: {e}")
            self._cfg = {}

        with self._lock:
            self._today_target = self._compute_today_target()

    def _compute_today_target(self) -> int:
        if not self._cfg.get("enabled", False):
            return 0
        try:
            start = date.fromisoformat(self._cfg["event_start_date"])
        except (KeyError, ValueError):
            return 0
        day_num = (date.today() - start).days
        offsets = self._cfg.get("day_offsets", [4000, 3000, 2000, 1000])
        if 0 <= day_num < len(offsets):
            return offsets[day_num]
        return 0

    def _restore_today_state(self):
        """Read today's supplemental IN/OUT counts from DB on startup."""
        today = date.today().isoformat()
        try:
            conn = sqlite3.connect(DB_PATH)
            cur  = conn.cursor()
            cur.execute("""
                SELECT
                    SUM(CASE WHEN event_type = 'GHOST_IN'  THEN 1 ELSE 0 END),
                    SUM(CASE WHEN event_type = 'GHOST_OUT' THEN 1 ELSE 0 END)
                FROM events
                WHERE date(timestamp) = ?
            """, (today,))
            row = cur.fetchone()
            conn.close()

            if row and row[0] is not None:
                self._extra_in_today  = int(row[0])
                self._extra_out_today = int(row[1])

            unmatched = self._extra_in_today - self._extra_out_today
            if unmatched > 0:
                avg_dwell = self._cfg.get("avg_dwell_time_seconds", 300)
                jitter    = self._cfg.get("dwell_jitter_seconds", 60)
                now       = time.time()
                for i in range(unmatched):
                    fire_at = now + avg_dwell + random.uniform(0, jitter * 2) + (i * 10)
                    self._pending_exits.append(fire_at)
                print(f"[ENGINE] Restored {unmatched} unmatched events into pending queue.")

        except Exception as e:
            print(f"[ENGINE] State restore error: {e}")

    def _write_event(self, direction: str):
        """Write a GHOST_IN or GHOST_OUT event to DB (called inside the lock)."""
        event_type = "GHOST_IN" if direction == "IN" else "GHOST_OUT"
        try:
            conn = sqlite3.connect(DB_PATH)
            conn.execute(
                "INSERT INTO events (timestamp, event_type, track_id, is_ghost) "
                "VALUES (datetime('now', 'localtime'), ?, ?, 1)",
                (event_type, -1),
            )
            conn.commit()
            conn.close()
        except Exception as e:
            print(f"[ENGINE] DB write error ({event_type}): {e}")

    def _update_live_status(self):
        """Push combined (real + supplemental) totals to live_status table."""
        try:
            with self._lock:
                extra_in  = self._extra_in_today
                extra_out = self._extra_out_today
                real_in   = self._last_real_in

            conn = sqlite3.connect(DB_PATH)
            cur  = conn.cursor()

            row = cur.execute(
                "SELECT total_in, total_out, active_tracks FROM live_status ORDER BY id DESC LIMIT 1"
            ).fetchone()

            if row:
                combined_in  = (row[0] or 0) + extra_in
                combined_out = (row[1] or 0) + extra_out
                active       = row[2] or 0
                cur.execute(
                    "INSERT INTO live_status (timestamp, active_tracks, total_in, total_out) "
                    "VALUES (datetime('now', 'localtime'), ?, ?, ?)",
                    (active, combined_in, combined_out),
                )
                conn.commit()
            conn.close()
        except Exception:
            pass  # Non-critical — dashboard will use last known value
