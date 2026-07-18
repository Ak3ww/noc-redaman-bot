import json
import sqlite3

conn = sqlite3.connect('backend/redaman.db')
sns = [r[0] for r in conn.execute('SELECT sn FROM onu_name_cache').fetchall() if r[0] and r[0].startswith('ZTEG')]
unmapped = json.load(open('scratch/mikrotik_unmapped.json'))

found = 0
for u in unmapped:
    mac = u.get('mac', '')
    if not mac: continue
    mac_hex = mac.replace(':', '').lower()[-8:]
    try:
        mac_int = int(mac_hex, 16)
    except:
        continue
    
    matched = False
    for s in sns:
        s_hex = s[-8:].lower()
        try:
            s_int = int(s_hex, 16)
        except:
            continue
        if abs(mac_int - s_int) <= 10:
            matched = True
            break
            
    if matched:
        found += 1

print(f'Found {found} fuzzy MAC-SN matches out of {len(unmapped)}')
