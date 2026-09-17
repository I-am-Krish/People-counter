import sqlite3
import datetime

conn = sqlite3.connect('analytics.db')
conn.row_factory = sqlite3.Row

live_status = conn.execute('SELECT * FROM live_status ORDER BY id DESC LIMIT 1').fetchone()
print("LIVE STATUS:", dict(live_status) if live_status else None)

today = '2026-09-17'
query = '''
    SELECT 
        strftime('%H:00', timestamp) as hour_bucket,
        SUM(CASE WHEN event_type = 'IN' THEN 1 ELSE 0 END) as in_count
    FROM events
    WHERE date(timestamp) = ?
    GROUP BY hour_bucket
    ORDER BY hour_bucket ASC
'''
rows = conn.execute(query, (today,)).fetchall()
print("CHART DATA:", [dict(r) for r in rows])
