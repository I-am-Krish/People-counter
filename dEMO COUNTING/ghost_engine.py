"""
ghost_engine.py
===============
Invisible Ghost Counter engine for the Ganesh Pandal People Counting System.

Injects synthetic IN/OUT events into analytics.db to inflate the total
daily visitor count toward a sliding daily target, without any trace
visible on the frontend. The combined (real + ghost) count is what the
live overlay and dashboard display.

Ceiling logic:
    Ghost IN stops when (real_in + ghost_in) >= absolute_ceiling (default 12,000).
    This uses the COMBINED visible count, not the raw real-only count.

Daily target schedule (configured via ghost_config.json day_offsets):
    Day 1: +4000 ghost INs
    Day 2: +3000 ghost INs
    Day 3: +2000 ghost INs
    Day 4: +1000 ghost INs
    Day 5+: 0 (ghost engine idles)

After ceiling/target is reached, all accumulated Ghost_INs without a
matching Ghost_OUT are drained gradually over time so occupancy falls
naturally and every ghost entrant eventually "leaves".
"""

import json
import os
import random
import sqlite3
import threading
import time
from collections import deque
from datetime import date, datetime


GHOST_CONFIG_PATH = os.path.join(os.path.dirname(__file__), "ghost_config.json")
DB_PATH = os.path.join(os.path.dirname(__file__), "analytics.db")

CONFIG_RELOAD_INTERVAL = 30   # seconds between config hot-reloads
DRAIN_CHECK_INTERVAL   = 2    # seconds between pending-exit checks


class GhostEngine:
    """
    Self-contained ghost counter. Run via start(); it spawns its own
    background daemon thread and never blocks the main camera loop.

    Public interface:
        notify_real_in(combined_real_in)   — call whenever a real IN crossing fires
        get_ghost_counts()                 — returns {"ghost_in": int, "ghost_out": int}
        is_injecting()                     — True while still adding ghost INs
    """

    def __init__(self):
        self._lock = threading.Lock()

        # Config (loaded in start())
        self._cfg = {}

        # Today's ghost state (restored from DB on start)
        self._ghost_in_today  = 0
        self._ghost_out_today = 0

        # Combined visible total fed from people_counter.py
        # We store the last reported real_in so we know the combined total
        self._last_real_in    = 0

        # Rolling window of REAL crossing timestamps for surge detection
        self._real_in_window: deque = deque()

        # Pending Ghost_OUTs: list of (fire_at_timestamp,)
        self._pending_exits: deque = deque()

        # State flags
        self._injecting = False   # True = still adding ghost INs
        self._draining  = False   # True = no new INs, draining exits
        self._active    = False   # False = master off or day offset = 0

        self._today_str    = date.today().isoformat()
        self._today_target = 0    # set after config load

        self._last_config_reload = 0.0
        self._thread: threading.Thread | None = None

    # ─── Public API ──────────────────────────────────────────────────────────

    def start(self):
        """Load config, restore today's state from DB, and start background thread."""
        self._load_config()
        self._restore_today_state()
        self._determine_mode()
        self._thread = threading.Thread(target=self._run_loop, daemon=True, name="GhostEngine")
        self._thread.start()
        print(f"[GHOST] Engine started | day_target={self._today_target} | "
              f"ghost_in_so_far={self._ghost_in_today} | injecting={self._injecting}")

    def notify_real_in(self, combined_real_in: int):
        """
        Called by people_counter.py whenever a new REAL IN crossing is detected.
        combined_real_in: the current total real IN count from SingleLineCounter.
        """
        with self._lock:
            self._last_real_in = combined_real_in

            if not self._injecting:
                return

            now = time.time()
            # Update rolling window
            self._real_in_window.append(now)

            # Prune entries outside the activity window
            window_sec = self._cfg.get("activity_window_seconds", 10)
            while self._real_in_window and (now - self._real_in_window[0]) > window_sec:
                self._real_in_window.popleft()

            real_in_window_count = len(self._real_in_window)
            threshold = self._cfg.get("trigger_threshold_people", 5)

            # Determine ghost bump size
            if real_in_window_count >= threshold * 2:
                # High rush
                bump = random.randint(4, 8)
            elif real_in_window_count >= threshold:
                # Normal surge
                bump = random.randint(1, 3)
            else:
                # Off-peak: 3% random chance of +1
                bump = 1 if random.random() < 0.03 else 0

            if bump > 0:
                self._inject_ghost_ins(bump, now)

    def get_ghost_counts(self) -> dict:
        """Returns {"ghost_in": int, "ghost_out": int} for today."""
        with self._lock:
            return {
                "ghost_in":  self._ghost_in_today,
                "ghost_out": self._ghost_out_today,
            }

    def is_injecting(self) -> bool:
        """True while ghost INs are still being added."""
        with self._lock:
            return self._injecting

    # ─── Internal Logic ──────────────────────────────────────────────────────

    def _combined_total_in(self) -> int:
        """Combined visible total = real_in + ghost_in."""
        return self._last_real_in + self._ghost_in_today

    def _determine_mode(self):
        """Set injecting/draining/active flags based on current state."""
        if not self._cfg.get("ghost_mode_enabled", False) or self._today_target == 0:
            self._active    = False
            self._injecting = False
            self._draining  = len(self._pending_exits) > 0
            return

        self._active = True

        ceiling_hit    = self._combined_total_in() >= self._cfg.get("absolute_ceiling", 12000)
        target_reached = self._ghost_in_today      >= self._today_target

        if ceiling_hit or target_reached:
            self._injecting = False
            self._draining  = self._ghost_in_today > self._ghost_out_today
        else:
            self._injecting = True
            self._draining  = False  # draining happens alongside injecting naturally

    def _inject_ghost_ins(self, count: int, now: float):
        """
        Clamp count so we don't exceed day target or absolute ceiling.
        Write each ghost IN to DB and schedule a matching delayed Ghost_OUT.
        Called inside the lock.
        """
        ceiling  = self._cfg.get("absolute_ceiling", 12000)
        combined = self._combined_total_in()

        # How many can we still add without busting the ceiling?
        ceiling_headroom = ceiling - combined
        # How many can we add without exceeding today's target?
        target_headroom  = self._today_target - self._ghost_in_today

        actual = min(count, ceiling_headroom, target_headroom)
        if actual <= 0:
            self._injecting = False
            self._draining  = self._ghost_in_today > self._ghost_out_today
            return

        avg_dwell  = self._cfg.get("avg_dwell_time_seconds", 300)
        jitter     = self._cfg.get("dwell_jitter_seconds", 60)

        for _ in range(actual):
            self._ghost_in_today += 1
            self._write_ghost_event("GHOST_IN")
            fire_at = now + avg_dwell + random.uniform(-jitter, jitter)
            self._pending_exits.append(fire_at)

        # Re-check ceiling after injection
        if self._combined_total_in() >= ceiling or self._ghost_in_today >= self._today_target:
            self._injecting = False
            self._draining  = self._ghost_in_today > self._ghost_out_today

    def _drain_pending_exits(self):
        """Fire any Ghost_OUTs whose scheduled time has passed. Called without lock."""
        now = time.time()
        fired = 0
        with self._lock:
            while self._pending_exits and self._pending_exits[0] <= now:
                self._pending_exits.popleft()
                self._ghost_out_today += 1
                self._write_ghost_event("GHOST_OUT")
                fired += 1
        if fired:
            print(f"[GHOST] Fired {fired} Ghost_OUT(s) | total ghost_out={self._ghost_out_today}")

    def _run_loop(self):
        """Background daemon loop: drain exits, hot-reload config, update live_status."""
        while True:
            try:
                self._drain_pending_exits()

                # Hot-reload config every CONFIG_RELOAD_INTERVAL seconds
                now = time.time()
                if now - self._last_config_reload >= CONFIG_RELOAD_INTERVAL:
                    self._load_config()
                    with self._lock:
                        self._today_target = self._compute_today_target()
                        self._determine_mode()
                    self._last_config_reload = now

                # Update live_status with combined totals every 5s
                self._update_live_status()

            except Exception as e:
                print(f"[GHOST] Loop error: {e}")

            time.sleep(DRAIN_CHECK_INTERVAL)

    # ─── Config & DB Helpers ─────────────────────────────────────────────────

    def _load_config(self):
        try:
            with open(GHOST_CONFIG_PATH, "r") as f:
                self._cfg = json.load(f)
        except Exception as e:
            print(f"[GHOST] Config load error: {e}")
            self._cfg = {}

        with self._lock:
            self._today_target = self._compute_today_target()

    def _compute_today_target(self) -> int:
        """
        Compute today's ghost IN target based on event_start_date and day_offsets.
        Returns 0 if ghost_mode_enabled is False or day is beyond the offsets list.
        """
        if not self._cfg.get("ghost_mode_enabled", False):
            return 0
        try:
            start = date.fromisoformat(self._cfg["event_start_date"])
        except (KeyError, ValueError):
            return 0
        day_num = (date.today() - start).days  # 0-indexed
        offsets = self._cfg.get("day_offsets", [4000, 3000, 2000, 1000])
        if 0 <= day_num < len(offsets):
            return offsets[day_num]
        return 0

    def _restore_today_state(self):
        """
        Read today's GHOST_IN and GHOST_OUT counts from the DB so restarts
        don't re-add already-counted ghost events.
        Also rebuild the pending_exits queue as any unmatched Ghost_INs
        from today that haven't been exited yet need to eventually exit.
        """
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
                self._ghost_in_today  = int(row[0])
                self._ghost_out_today = int(row[1])
            else:
                self._ghost_in_today  = 0
                self._ghost_out_today = 0

            # Re-queue exits for unmatched ghost INs (spread them out from now)
            unmatched = self._ghost_in_today - self._ghost_out_today
            if unmatched > 0:
                avg_dwell = self._cfg.get("avg_dwell_time_seconds", 300)
                jitter    = self._cfg.get("dwell_jitter_seconds", 60)
                now = time.time()
                for i in range(unmatched):
                    # Spread them out over time so they don't all fire at once
                    fire_at = now + avg_dwell + random.uniform(0, jitter * 2) + (i * 10)
                    self._pending_exits.append(fire_at)
                print(f"[GHOST] Restored {unmatched} unmatched Ghost_INs into pending_exits queue.")

        except Exception as e:
            print(f"[GHOST] State restore error: {e}")

    def _write_ghost_event(self, event_type: str):
        """Write a single GHOST_IN or GHOST_OUT event to DB. Called inside the lock."""
        try:
            conn = sqlite3.connect(DB_PATH)
            conn.execute(
                "INSERT INTO events (timestamp, event_type, track_id, is_ghost) "
                "VALUES (datetime('now', 'localtime'), ?, ?, 1)",
                (event_type, -1)
            )
            conn.commit()
            conn.close()
        except Exception as e:
            print(f"[GHOST] DB write error ({event_type}): {e}")

    def _update_live_status(self):
        """
        Update the live_status table with the combined (real + ghost) totals
        so the analytics server always reads up-to-date naturalized numbers.
        """
        try:
            with self._lock:
                ghost_in  = self._ghost_in_today
                ghost_out = self._ghost_out_today
                real_in   = self._last_real_in

            today = date.today().isoformat()
            conn  = sqlite3.connect(DB_PATH)
            cur   = conn.cursor()

            # Read real_out from live_status (people_counter writes it)
            row = cur.execute(
                "SELECT total_in, total_out, active_tracks FROM live_status ORDER BY id DESC LIMIT 1"
            ).fetchone()

            if row:
                combined_in  = (row[0] or 0) + ghost_in
                combined_out = (row[1] or 0) + ghost_out
                active       = row[2] or 0
                cur.execute(
                    "INSERT INTO live_status (timestamp, active_tracks, total_in, total_out) "
                    "VALUES (datetime('now', 'localtime'), ?, ?, ?)",
                    (active, combined_in, combined_out)
                )
                conn.commit()
            conn.close()
        except Exception:
            pass  # Non-critical; analytics server will just use last known value
