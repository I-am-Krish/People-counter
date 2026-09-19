"""
dual_line_counter.py
====================
Single-line directional people counter with hysteresis dead zone and
persistent SQLite event logging.

A crossing is registered only when the tracked person's anchor moves from
one side of the counting line to the other AND is at least `dead_zone`
pixels away from the line — preventing jitter-based false counts.

After a crossing a cooldown period prevents duplicate registrations.
"""

import sqlite3
import time


# ─── GEOMETRY ────────────────────────────────────────────────────────────────


def signed_distance_to_line(line_start, line_end, point) -> float:
    """
    Returns the signed perpendicular distance from *point* to the line
    defined by *line_start* → *line_end*.

    For a horizontal Right-to-Left line:
        Positive = ABOVE the line
        Negative = BELOW the line
    """
    ax, ay = line_start
    bx, by = line_end
    px, py = point

    dx = bx - ax
    dy = by - ay
    length = (dx * dx + dy * dy) ** 0.5
    if length == 0:
        return 0.0

    return ((dx) * (py - ay) - (dy) * (px - ax)) / length


# ─── COUNTER ─────────────────────────────────────────────────────────────────


class SingleLineCounter:
    """
    Directional people counter for a single virtual line.

    Parameters
    ----------
    line_start, line_end : tuple[float, float]
        Pixel coordinates of the counting line endpoints.
    dead_zone : int
        Minimum distance (px) from the line before a side is confirmed.
    cooldown_frames : int
        Frames a track must wait before another crossing can be registered.
    timeout_seconds : float
        Seconds after which an unseen track is removed from state.
    db_path : str
        Path to the SQLite analytics database.
    """

    def __init__(
        self,
        line_start,
        line_end,
        dead_zone: int = 15,
        cooldown_frames: int = 30,
        timeout_seconds: float = 5.0,
        db_path: str = "analytics.db",
    ):
        self.line             = (line_start, line_end)
        self.dead_zone        = dead_zone
        self.cooldown_frames  = cooldown_frames
        self.timeout_seconds  = timeout_seconds

        self.entered_count = 0
        self.exited_count  = 0

        self.tracks       = {}   # tracker_id → state dict
        self._frame_count = 0

        self.db_path = db_path
        self._init_db()

    # ─── DB helpers ──────────────────────────────────────────────────────────

    def _init_db(self):
        try:
            with sqlite3.connect(self.db_path) as conn:
                cur = conn.cursor()

                # Events table
                cur.execute("""
                    CREATE TABLE IF NOT EXISTS events (
                        id         INTEGER PRIMARY KEY AUTOINCREMENT,
                        timestamp  DATETIME DEFAULT (datetime('now', 'localtime')),
                        event_type TEXT,
                        track_id   INTEGER,
                        is_ghost   INTEGER DEFAULT 0
                    )
                """)

                # Migrate: add is_ghost column on older DBs
                try:
                    cur.execute("ALTER TABLE events ADD COLUMN is_ghost INTEGER DEFAULT 0")
                except Exception:
                    pass  # Column already exists — safe to ignore

                # Live-status table (polled by analytics dashboard)
                cur.execute("""
                    CREATE TABLE IF NOT EXISTS live_status (
                        id            INTEGER PRIMARY KEY AUTOINCREMENT,
                        timestamp     DATETIME DEFAULT (datetime('now', 'localtime')),
                        active_tracks INTEGER,
                        total_in      INTEGER,
                        total_out     INTEGER
                    )
                """)

                # Restore today's running count so a restart doesn't reset to 0
                cur.execute("""
                    SELECT
                        SUM(CASE WHEN event_type = 'IN'  THEN 1 ELSE 0 END),
                        SUM(CASE WHEN event_type = 'OUT' THEN 1 ELSE 0 END)
                    FROM events
                    WHERE date(timestamp) = date('now', 'localtime')
                """)
                row = cur.fetchone()
                if row and row[0] is not None:
                    self.entered_count = int(row[0])
                    self.exited_count  = int(row[1])

                conn.commit()
        except Exception as e:
            print(f"[COUNTER] DB init error: {e}")

    def _log_event(self, event_type: str, track_id: int):
        try:
            with sqlite3.connect(self.db_path) as conn:
                conn.execute(
                    "INSERT INTO events (timestamp, event_type, track_id) "
                    "VALUES (datetime('now', 'localtime'), ?, ?)",
                    (event_type, track_id),
                )
                conn.commit()
        except Exception as e:
            print(f"[COUNTER] DB log error ({event_type}, track {track_id}): {e}")

    def log_status(self, active_tracks: int):
        """Write the current real counts to live_status (polled by dashboard)."""
        try:
            with sqlite3.connect(self.db_path) as conn:
                conn.execute(
                    "INSERT INTO live_status (timestamp, active_tracks, total_in, total_out) "
                    "VALUES (datetime('now', 'localtime'), ?, ?, ?)",
                    (active_tracks, self.entered_count, self.exited_count),
                )
                conn.commit()
        except Exception as e:
            print(f"[COUNTER] DB status write error: {e}")

    # ─── Geometry ────────────────────────────────────────────────────────────

    def _get_side(self, point) -> int:
        """
        Returns:
            1  — clearly above the line
            -1 — clearly below the line
            0  — inside the dead zone (ambiguous; ignore)
        """
        dist = signed_distance_to_line(self.line[0], self.line[1], point)
        if dist > self.dead_zone:
            return 1
        elif dist < -self.dead_zone:
            return -1
        return 0

    # ─── Main update ─────────────────────────────────────────────────────────

    def update(self, detections):
        """
        Update the counter state machine for the current frame.

        Parameters
        ----------
        detections : sv.Detections
            Detections object with *tracker_id* populated by ByteTrack.
        """
        current_time = time.time()
        self._frame_count += 1

        # Remove tracks that haven't been seen for longer than the timeout
        stale = [
            tid for tid, data in self.tracks.items()
            if current_time - data["last_seen"] > self.timeout_seconds
        ]
        for tid in stale:
            del self.tracks[tid]

        if detections.tracker_id is None or len(detections) == 0:
            return

        for i in range(len(detections)):
            tracker_id   = int(detections.tracker_id[i])
            x1, y1, x2, y2 = detections.xyxy[i]

            # Use bounding-box centre as the anchor point
            px = (x1 + x2) / 2.0
            py = (y1 + y2) / 2.0
            point        = (px, py)
            current_side = self._get_side(point)

            if tracker_id not in self.tracks:
                # Register only if the track starts clearly on one side
                if current_side != 0:
                    self.tracks[tracker_id] = {
                        "confirmed_side": current_side,
                        "cooldown":       0,
                        "last_seen":      current_time,
                    }
                continue

            track = self.tracks[tracker_id]
            track["last_seen"] = current_time

            # In dead zone — skip, but preserve last state
            if current_side == 0:
                continue

            # In cooldown — decrement and update side but don't check crossing
            if track["cooldown"] > 0:
                track["cooldown"] -= 1
                track["confirmed_side"] = current_side
                continue

            confirmed_side = track["confirmed_side"]

            # Register crossing only when the side genuinely flipped
            if confirmed_side != 0 and confirmed_side != current_side:
                if confirmed_side == 1 and current_side == -1:
                    # Above → Below = ENTRY
                    self.entered_count     += 1
                    track["cooldown"]       = self.cooldown_frames
                    self._log_event("IN", tracker_id)
                    print(f"  *** Track {tracker_id}: ENTERED ({px:.0f},{py:.0f}) "
                          f"Total IN={self.entered_count}")

                elif confirmed_side == -1 and current_side == 1:
                    # Below → Above = EXIT
                    self.exited_count      += 1
                    track["cooldown"]       = self.cooldown_frames
                    self._log_event("OUT", tracker_id)
                    print(f"  *** Track {tracker_id}: EXITED  ({px:.0f},{py:.0f}) "
                          f"Total OUT={self.exited_count}")

            track["confirmed_side"] = current_side
