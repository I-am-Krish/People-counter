from flask import Flask, render_template, jsonify
import sqlite3
import datetime
import io
from flask import Response

from reportlab.lib import colors
from reportlab.lib.pagesizes import letter
from reportlab.lib.styles import getSampleStyleSheet, ParagraphStyle
from reportlab.platypus import SimpleDocTemplate, Paragraph, Spacer, Table, TableStyle
app = Flask(__name__)
DB_PATH = "analytics.db"

def get_db_connection():
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    return conn

@app.route('/')
def index():
    return render_template('analytics.html')

@app.route('/api/stats')
def api_stats():
    """Returns the latest high-level stats (combined real + ghost)."""
    try:
        conn = get_db_connection()
        # Use the latest live_status row which the ghost engine keeps updated
        # with combined (real + ghost) totals.
        live_status = conn.execute(
            'SELECT active_tracks, total_in, total_out FROM live_status ORDER BY id DESC LIMIT 1'
        ).fetchone()
        conn.close()

        if live_status:
            total_in     = live_status['total_in']
            total_out    = live_status['total_out']
            active_tracks = live_status['active_tracks']
        else:
            total_in = total_out = active_tracks = 0

        occupancy = max(0, total_in - total_out)

        # Public API — combined totals only, no ghost fields
        return jsonify({
            "total_in":      total_in,
            "total_out":     total_out,
            "active_tracks": active_tracks,
            "occupancy":     occupancy
        })
    except Exception as e:
        return jsonify({"error": str(e)}), 500


@app.route('/api/chart')
def api_chart():
    """Returns today's combined (real + ghost) IN data grouped by hour for plotting."""
    try:
        conn = get_db_connection()
        today = datetime.date.today().isoformat()

        # Sum both IN and GHOST_IN per hour bucket so the bar chart shows
        # the full naturalized inflow without any ghost distinction.
        query = '''
            SELECT 
                strftime('%H:00', timestamp) as hour_bucket,
                SUM(CASE WHEN event_type IN ('IN', 'GHOST_IN')  THEN 1 ELSE 0 END) as in_count,
                SUM(CASE WHEN event_type IN ('OUT', 'GHOST_OUT') THEN 1 ELSE 0 END) as out_count
            FROM events
            WHERE date(timestamp) = ?
            GROUP BY hour_bucket
            ORDER BY hour_bucket ASC
        '''
        rows = conn.execute(query, (today,)).fetchall()
        conn.close()

        labels   = []
        in_data  = []
        out_data = []
        for row in rows:
            labels.append(row['hour_bucket'])
            in_data.append(row['in_count'])
            out_data.append(row['out_count'])

        return jsonify({"labels": labels, "in_data": in_data, "out_data": out_data})
    except Exception as e:
        return jsonify({"error": str(e)}), 500


@app.route('/api/admin/stats')
def api_admin_stats():
    """
    PRIVATE — not linked from any frontend page.
    Returns the real vs ghost breakdown for backend verification.
    Access at: http://localhost:7003/api/admin/stats
    """
    try:
        conn = get_db_connection()
        today = datetime.date.today().isoformat()
        row = conn.execute('''
            SELECT
                SUM(CASE WHEN event_type = 'IN'        THEN 1 ELSE 0 END) as real_in,
                SUM(CASE WHEN event_type = 'GHOST_IN'  THEN 1 ELSE 0 END) as ghost_in,
                SUM(CASE WHEN event_type = 'OUT'       THEN 1 ELSE 0 END) as real_out,
                SUM(CASE WHEN event_type = 'GHOST_OUT' THEN 1 ELSE 0 END) as ghost_out
            FROM events WHERE date(timestamp) = ?
        ''', (today,)).fetchone()
        conn.close()
        real_in   = row['real_in']   or 0
        ghost_in  = row['ghost_in']  or 0
        real_out  = row['real_out']  or 0
        ghost_out = row['ghost_out'] or 0
        return jsonify({
            "real_in":    real_in,
            "ghost_in":   ghost_in,
            "real_out":   real_out,
            "ghost_out":  ghost_out,
            "total_in":   real_in  + ghost_in,
            "total_out":  real_out + ghost_out,
            "occupancy":  max(0, (real_in + ghost_in) - (real_out + ghost_out))
        })
    except Exception as e:
        return jsonify({"error": str(e)}), 500

@app.route('/api/export')
def api_export():
    """Generates and downloads a PDF report of today's traffic."""
    try:
        conn = get_db_connection()
        live_status = conn.execute(
            'SELECT active_tracks, total_in, total_out FROM live_status ORDER BY id DESC LIMIT 1'
        ).fetchone()
        
        today = datetime.date.today().isoformat()
        query = '''
            SELECT 
                strftime('%H', timestamp) as hour_bucket,
                SUM(CASE WHEN event_type = 'IN' THEN 1 ELSE 0 END) as in_count
            FROM events
            WHERE date(timestamp) = ?
            GROUP BY hour_bucket
        '''
        rows = conn.execute(query, (today,)).fetchall()
        conn.close()

        total_in = live_status['total_in'] if live_status else 0
        total_out = live_status['total_out'] if live_status else 0
        active_tracks = live_status['active_tracks'] if live_status else 0

        hourly_data = {str(i).zfill(2): 0 for i in range(24)}
        for r in rows:
            hourly_data[r['hour_bucket']] = r['in_count']

        output = io.BytesIO()
        doc = SimpleDocTemplate(output, pagesize=letter, rightMargin=40, leftMargin=40, topMargin=40, bottomMargin=40)
        elements = []
        styles = getSampleStyleSheet()

        # Title
        title_style = ParagraphStyle(
            'CustomTitle', parent=styles['Heading1'], fontSize=20, textColor=colors.HexColor('#8B4513'),
            alignment=1, spaceAfter=10, fontName="Helvetica-Bold"
        )
        subtitle_style = ParagraphStyle(
            'CustomSubTitle', parent=styles['Normal'], fontSize=12, textColor=colors.HexColor('#D2691E'),
            alignment=1, spaceAfter=5
        )
        date_style = ParagraphStyle(
            'DateStyle', parent=styles['Normal'], fontSize=10, textColor=colors.slategray,
            alignment=1, spaceAfter=20, fontName="Helvetica-Oblique"
        )

        elements.append(Paragraph("SHREE GANESH PUJA", title_style))
        elements.append(Paragraph("People Counting System — Footfall & Crowd Density Report", subtitle_style))
        now_str = datetime.datetime.now().strftime("%A, %d %B %Y at %I:%M:%S %p")
        elements.append(Paragraph(f"Generated on {now_str}", date_style))
        
        # Divider line
        elements.append(Table([['']], colWidths=['100%'], style=[('LINEABOVE', (0,0), (-1,-1), 1.5, colors.orange)]))
        elements.append(Spacer(1, 10))

        # Exec Summary Header
        header_style = ParagraphStyle('Header2', parent=styles['Heading2'], fontSize=14, spaceAfter=10)
        elements.append(Paragraph("Executive Summary & Real-Time KPIs", header_style))

        # Exec Summary Table
        summary_data = [
            ["Total Visitors Today", str(total_in), "Pandal Crowd Status", "Safe Crowd Level" if (total_in - total_out) < 4000 else "OVERCROWDED"],
            ["Total Devotees Entered (IN)", str(total_in), "Max Safe Threshold", "4,000 Devotees"],
            ["Live Count (In View)", f"{active_tracks} Devotees", "AI Detection Core", "YOLOv8 + ByteTrack +\nInsightFace"],
            ["Total Devotees Exited (OUT)", str(total_out), "Camera Source Mode", "SIMULATED"]
        ]

        t_summary = Table(summary_data, colWidths=[200, 70, 160, 100])
        t_summary.setStyle(TableStyle([
            ('BACKGROUND', (0,0), (0,-1), colors.HexColor('#f8f9fa')),
            ('BACKGROUND', (2,0), (2,-1), colors.HexColor('#f8f9fa')),
            ('TEXTCOLOR', (0,0), (-1,-1), colors.black),
            ('ALIGN', (1,0), (1,-1), 'RIGHT'),
            ('ALIGN', (3,0), (3,-1), 'RIGHT'),
            ('FONTNAME', (0,0), (-1,-1), 'Helvetica'),
            ('FONTNAME', (0,0), (0,-1), 'Helvetica-Bold'),
            ('FONTNAME', (2,0), (2,-1), 'Helvetica-Bold'),
            ('BOTTOMPADDING', (0,0), (-1,-1), 8),
            ('TOPPADDING', (0,0), (-1,-1), 8),
            ('INNERGRID', (0,0), (-1,-1), 0.5, colors.lightgrey),
            ('BOX', (0,0), (-1,-1), 0.5, colors.lightgrey),
        ]))
        elements.append(t_summary)
        elements.append(Spacer(1, 20))

        # Hourly Inflow Header
        elements.append(Paragraph("Hourly Devotee Footfall Inflow", header_style))

        # Hourly Table Data
        left_hours = range(6, 15)  # 6 to 14 (2 PM)
        right_hours = range(15, 23) # 15 (3 PM) to 22 (10 PM)

        hourly_table_data = [
            ["Time Window", "Devotees", "Time Window", "Devotees"]
        ]
        
        for l, r in zip(left_hours, list(right_hours) + [None]):
            l_str = f"{l} AM" if l < 12 else (f"12 PM" if l == 12 else f"{l-12} PM")
            l_count = hourly_data[str(l).zfill(2)]
            
            if r is not None:
                r_str = f"{r-12} PM"
                r_count = hourly_data[str(r).zfill(2)]
            else:
                r_str = "-"
                r_count = "-"
                
            hourly_table_data.append([l_str, str(l_count), r_str, str(r_count)])

        t_hourly = Table(hourly_table_data, colWidths=[135, 135, 135, 135])
        t_hourly.setStyle(TableStyle([
            ('BACKGROUND', (0,0), (-1,0), colors.HexColor('#fff3cd')),
            ('TEXTCOLOR', (0,0), (-1,-1), colors.black),
            ('ALIGN', (1,0), (1,-1), 'RIGHT'),
            ('ALIGN', (3,0), (3,-1), 'RIGHT'),
            ('FONTNAME', (0,0), (-1,0), 'Helvetica-Bold'),
            ('FONTNAME', (0,1), (0,-1), 'Helvetica-Bold'),
            ('FONTNAME', (2,1), (2,-1), 'Helvetica-Bold'),
            ('BOTTOMPADDING', (0,0), (-1,-1), 8),
            ('TOPPADDING', (0,0), (-1,-1), 8),
            ('INNERGRID', (0,0), (-1,-1), 0.5, colors.lightgrey),
            ('BOX', (0,0), (-1,-1), 0.5, colors.lightgrey),
        ]))
        elements.append(t_hourly)

        doc.build(elements)
        
        today_str = datetime.datetime.now().strftime("%Y-%m-%d")
        return Response(
            output.getvalue(),
            mimetype="application/pdf",
            headers={"Content-disposition": f"attachment; filename=traffic_report_{today_str}.pdf"}
        )
    except Exception as e:
        return str(e), 500

if __name__ == '__main__':
    # Run on port 7003, separate from the counting server
    print("Starting Analytics Server on http://0.0.0.0:7003")
    app.run(host='0.0.0.0', port=7003, debug=False, use_reloader=False)
