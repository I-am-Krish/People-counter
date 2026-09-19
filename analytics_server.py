"""
analytics_server.py
====================
Analytics dashboard Flask server for the People Counting System.

Serves the web dashboard (port 7003 by default) and exposes a small REST
API consumed by the dashboard's JavaScript.

Routes
------
GET /              → HTML dashboard (templates/analytics.html)
GET /api/stats     → Live KPIs (total_in, total_out, occupancy, active_tracks)
GET /api/chart     → Hourly inflow data for the bar chart
"""

import datetime
import os
import sqlite3

from flask import Flask, Response, jsonify, render_template

from config import ANALYTICS_PORT, DB_PATH

app = Flask(__name__)
app.config['TEMPLATES_AUTO_RELOAD'] = True
app.config['SEND_FILE_MAX_AGE_DEFAULT'] = 0


# ─── DB HELPER ───────────────────────────────────────────────────────────────


def get_db_connection():
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    return conn


# ─── ROUTES ──────────────────────────────────────────────────────────────────


@app.route("/")
def index():
    from flask import make_response
    resp = make_response(render_template("analytics.html"))
    resp.headers['Cache-Control'] = 'no-store, no-cache, must-revalidate, max-age=0'
    resp.headers['Pragma'] = 'no-cache'
    resp.headers['Expires'] = '0'
    return resp


@app.route("/api/stats")
def api_stats():
    """Returns the latest high-level KPIs (combined real + supplemental)."""
    try:
        conn        = get_db_connection()
        live_status = conn.execute(
            "SELECT active_tracks, total_in, total_out "
            "FROM live_status ORDER BY id DESC LIMIT 1"
        ).fetchone()
        conn.close()

        if live_status:
            total_in      = live_status["total_in"]
            total_out     = live_status["total_out"]
            active_tracks = live_status["active_tracks"]
        else:
            total_in = total_out = active_tracks = 0

        occupancy = max(0, total_in - total_out)
        return jsonify(
            {
                "total_in":      total_in,
                "total_out":     total_out,
                "active_tracks": active_tracks,
                "occupancy":     occupancy,
            }
        )
    except Exception as e:
        return jsonify({"error": str(e)}), 500


@app.route("/api/chart")
def api_chart():
    """Returns today's combined inflow grouped by hour for the bar chart."""
    try:
        conn  = get_db_connection()
        today = datetime.date.today().isoformat()

        query = """
            SELECT
                strftime('%H:00', timestamp)                                      AS hour_bucket,
                SUM(CASE WHEN event_type IN ('IN', 'GHOST_IN')   THEN 1 ELSE 0 END) AS in_count,
                SUM(CASE WHEN event_type IN ('OUT', 'GHOST_OUT') THEN 1 ELSE 0 END) AS out_count
            FROM events
            WHERE date(timestamp) = ?
            GROUP BY hour_bucket
            ORDER BY hour_bucket ASC
        """
        rows = conn.execute(query, (today,)).fetchall()
        conn.close()

        labels, in_data, out_data = [], [], []
        for row in rows:
            labels.append(row["hour_bucket"])
            in_data.append(row["in_count"])
            out_data.append(row["out_count"])

        return jsonify({"labels": labels, "in_data": in_data, "out_data": out_data})
    except Exception as e:
        return jsonify({"error": str(e)}), 500


# ─── ENTRY POINT ─────────────────────────────────────────────────────────────

if __name__ == "__main__":
    print(f"Starting Analytics Dashboard on http://0.0.0.0:{ANALYTICS_PORT}")
    app.run(host="0.0.0.0", port=ANALYTICS_PORT, debug=False, use_reloader=False)
