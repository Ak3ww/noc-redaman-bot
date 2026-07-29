import sqlite3
import os
import sys

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
DB_FILE = os.path.join(BASE_DIR, 'redaman.db')

conn = sqlite3.connect(DB_FILE)
c = conn.cursor()

print("=== CHECK LATEST ATTENUATIONS ===")
c.execute("""
    SELECT a.olt_id, o.name, a.onu_id, c.customer_name, a.rx_power, a.timestamp 
    FROM attenuations a
    JOIN olts o ON a.olt_id = o.id
    LEFT JOIN onu_name_cache c ON a.onu_id = c.onu_id AND a.olt_id = c.olt_id
    WHERE a.id IN (SELECT MAX(id) FROM attenuations GROUP BY olt_id, onu_id)
    ORDER BY a.timestamp DESC
    LIMIT 10
""")

rows = c.fetchall()
for r in rows:
    print(f"OLT {r[0]} ({r[1]}) | ONU {r[2]} ({r[3]}) | Rx: {r[4]} dBm | Timestamp: {r[5]}")

print("\n=== CHECK SPECIFIC ONU (DIAN RUSDIANSYAH / 2.46) ===")
c.execute("""
    SELECT a.olt_id, a.onu_id, a.rx_power, a.timestamp 
    FROM attenuations a
    WHERE a.olt_id = 3 AND a.onu_id = '2.46'
    ORDER BY a.timestamp DESC
    LIMIT 5
""")
dian_rows = c.fetchall()
for r in dian_rows:
    print(f"Rx: {r[2]} dBm | Timestamp: {r[3]}")
