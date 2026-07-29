import sqlite3
import sys
import os
import difflib

# Gunakan path dinamis agar jalan di VPS Ubuntu maupun Windows
BASE_DIR = os.path.dirname(os.path.abspath(__file__))
sys.path.append(BASE_DIR)
from mikrotik_client import get_mikrotik_data

def clean_name(n):
    return n.upper().replace('PELANGGAN:', '').strip()

DB_FILE = os.path.join(BASE_DIR, 'redaman.db')
conn = sqlite3.connect(DB_FILE)
c = conn.cursor()

c.execute('SELECT onu_id, olt_id, customer_name, sn, pppoe_username FROM onu_name_cache')
olt_records = c.fetchall()

active, queues, secrets = get_mikrotik_data()

# Prepare Mikrotik names
mikrotik_names = {}
for comment, secret in secrets.items():
    if comment:  # Pastikan comment tidak kosong
        m_name = clean_name(comment)
        mikrotik_names[m_name] = secret

print(f"DEBUG: Ditemukan {len(secrets)} secrets dari Mikrotik.")
print(f"DEBUG: Ditemukan {len(mikrotik_names)} komentar valid dari Mikrotik.")

print("=== DAFTAR NAMA DI OLT YANG TIDAK DITEMUKAN / TYPO DI MIKROTIK ===")
unmatched = []

for onu_id, olt_id, customer_name, sn, pppoe in olt_records:
    # Skip empty names or default names
    if not customer_name or 'ZTEG' in customer_name.upper() or 'SKYW' in customer_name.upper() or 'ALCL' in customer_name.upper() or 'HWTC' in customer_name.upper():
        continue
        
    c_name_clean = clean_name(customer_name)
    
    # Check exact match
    if c_name_clean in mikrotik_names:
        continue
        
    # Check rough match
    best_match = None
    best_ratio = 0
    for m_name in mikrotik_names.keys():
        ratio = difflib.SequenceMatcher(None, c_name_clean, m_name).ratio()
        if ratio > best_ratio:
            best_ratio = ratio
            best_match = m_name
            
    # If no exact match, and best match is suspicious (ratio < 0.95)
    # We will just list anything that is not an EXACT match, but provide the closest suggestion!
    # To reduce noise, if ratio is very high (>0.85) it's likely just a typo.
    
    c.execute('SELECT name FROM olts WHERE id=?', (olt_id,))
    olt_str = c.fetchone()[0]
    
    unmatched.append({
        'olt': olt_str,
        'port': onu_id,
        'olt_name': customer_name,
        'closest_mikrotik': best_match,
        'ratio': best_ratio,
        'sn': sn
    })

# Sort by ratio descending so we see the obvious typos first
unmatched.sort(key=lambda x: x['ratio'], reverse=True)

count = 0
for u in unmatched:
    if u['ratio'] >= 0.6:
        print(f"[{u['olt']} Port {u['port']}] OLT: '{u['olt_name']}' --> Typo dari Mikrotik: '{u['closest_mikrotik']}' (Kemiripan: {int(u['ratio']*100)}%)")
        count += 1
    else:
        # Ratio is too low, probably completely missing from Mikrotik
        print(f"[{u['olt']} Port {u['port']}] OLT: '{u['olt_name']}' --> TIDAK ADA DI MIKROTIK SAMA SEKALI! (Paling mirip: '{u['closest_mikrotik']}')")
        count += 1

print(f"\nTotal Unmatched/Typo: {count}")
