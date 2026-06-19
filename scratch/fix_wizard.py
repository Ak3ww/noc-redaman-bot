with open('C:/BotRedaman/scratch/build_installer.py', 'r', encoding='utf-8') as f:
    c = f.read()

# 1. Change Mikrotik default IP
c = c.replace('Masukkan Mikrotik IP/Host [192.168.30.1]:', 'Masukkan Mikrotik IP/Host (Publik/VPN) [10.10.10.1]:')
c = c.replace('MK_HOST=${MK_HOST:-"192.168.30.1"}', 'MK_HOST=${MK_HOST:-"10.10.10.1"}')

# 2. Change the hardcoded OLT seed to an interactive python script
old_seed = """# 6a. Auto Seed Database (Default OLTs)
echo -e "\\n${BLUE}[6a/8] Auto-seed Database Redaman (Mendaftarkan OLT default)...${NC}"
cat << 'EOF_PY' > seed_db.py
import sqlite3
import os
conn = sqlite3.connect('redaman.db')
conn.execute('''CREATE TABLE IF NOT EXISTS olts (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    name TEXT NOT NULL,
    ip_port TEXT NOT NULL,
    brand TEXT NOT NULL,
    community TEXT DEFAULT 'public'
)''')
if conn.execute('SELECT count(*) FROM olts').fetchone()[0] == 0:
    conn.executemany('INSERT INTO olts (name, ip_port, brand, community) VALUES (?,?,?,?)', [
        ('GGCLINK-01', '192.168.30.3:8001', 'GGCLINK', 'public'),
        ('GGCLINK-02', '192.168.30.5:8002', 'GGCLINK', 'public'),
        ('HSGQ-G02ID', '192.168.30.4:161', 'HSGQ', 'public'),
        ('VSOL-GPON', '192.168.30.6:161', 'VSOL', 'public')
    ])
conn.commit()
conn.close()
EOF_PY
python3 seed_db.py
rm seed_db.py
echo -e "${GREEN}✔ Database berhasil di-seed.${NC}"""

new_seed = """# 6a. Wizard OLT Interaktif
echo -e "\\n${BLUE}[6a/8] Konfigurasi OLT (Interactive Wizard)...${NC}"
cat << 'EOF_PY' > seed_db.py
import sqlite3
import os

conn = sqlite3.connect('redaman.db')
conn.execute('''CREATE TABLE IF NOT EXISTS olts (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    name TEXT NOT NULL,
    ip_port TEXT NOT NULL,
    brand TEXT NOT NULL,
    community TEXT DEFAULT 'public'
)''')

print("\\n--- TAMBAH OLT KE DATABASE ---")
print("Biarkan Nama OLT KOSONG lalu tekan ENTER untuk selesai/melewati.\\n")

while True:
    try:
        name = input("Masukkan Nama OLT (contoh: OLT-Pusat) [Kosong = Selesai]: ").strip()
        if not name:
            break
        ip_port = input(f"Masukkan IP:Port untuk {name} (contoh: 103.X.X.X:161): ").strip()
        brand = input(f"Masukkan Merk OLT (VSOL/HSGQ/GGCLINK): ").strip().upper()
        
        conn.execute('INSERT INTO olts (name, ip_port, brand, community) VALUES (?,?,?,?)', 
                     (name, ip_port, brand, 'public'))
        conn.commit()
        print(f"[+] OLT {name} berhasil disimpan!\\n")
    except KeyboardInterrupt:
        break
    except EOFError:
        break

conn.close()
EOF_PY
python3 seed_db.py
rm seed_db.py
echo -e "${GREEN}✔ Konfigurasi OLT selesai.${NC}"""

c = c.replace(old_seed, new_seed)

with open('C:/BotRedaman/scratch/build_installer.py', 'w', encoding='utf-8') as f:
    f.write(c)

print("Patch applied successfully!")
