import os
import tarfile
import base64
import io

deployment_dir = r"c:\BotRedaman\deployment"
output_installer = r"c:\BotRedaman\noc-bot-installer.sh"

# Define files to include in the tarball
files_to_include = [
    "collector.py",
    "telegram_bot.py",
    "server.js",
    "database.py",
    "package.json",
    "requirements.txt",
    "dist"
]

def make_tarfile():
    tar_io = io.BytesIO()
    with tarfile.open(fileobj=tar_io, mode="w:gz") as tar:
        for fname in files_to_include:
            fpath = os.path.join(deployment_dir, fname)
            if os.path.exists(fpath):
                # Add file or directory, preserving relative path
                tar.add(fpath, arcname=fname)
            else:
                print(f"Warning: {fname} not found in deployment dir.")
    return tar_io.getvalue()

def generate_installer():
    print("Packing files...")
    tar_data = make_tarfile()
    encoded_payload = base64.b64encode(tar_data).decode("utf-8")
    
    # Format base64 payload into lines of 76 characters for clean formatting
    payload_lines = [encoded_payload[i:i+76] for i in range(0, len(encoded_payload), 76)]
    formatted_payload = "\n".join(payload_lines)
    
    print(f"Payload size (compressed & encoded): {len(formatted_payload)} bytes")
    
    # Standard shell script template
    script_content = """#!/bin/bash
# ==============================================================================
# NOC Bot Redaman - Standalone Auto Installer for Ubuntu VPS (Oracle Free Tier)
# ==============================================================================

# Definisikan warna untuk printout
GREEN='\\033[0;32m'
RED='\\033[0;31m'
YELLOW='\\033[1;33m'
BLUE='\\033[0;34m'
NC='\\033[0m' # No Color

echo -e "${BLUE}=====================================================${NC}"
echo -e "${GREEN}🚀 NOC BOT REDAMAN - VPS STANDALONE AUTO INSTALLER 🚀${NC}"
echo -e "${BLUE}=====================================================${NC}"

# 1. Pastikan tidak dijalankan langsung sebagai root (gunakan user biasa dengan sudo)
if [ "$EFFECTIVE_USER_ID" = "0" ] || [ "$EUID" = "0" ]; then
    echo -e "${RED}❌ JANGAN jalankan script ini langsung sebagai user 'root'.${NC}"
    echo -e "${YELLOW}Silakan jalankan sebagai user biasa (misalnya user 'ubuntu') menggunakan:${NC}"
    echo -e "   bash $(basename "$0")"
    exit 1
fi

INSTALL_DIR=$(pwd)
CURRENT_USER=$USER
USER_HOME=$HOME

echo -e "${YELLOW}Direktori instalasi: ${INSTALL_DIR}${NC}"
echo -e "${YELLOW}User running       : ${CURRENT_USER}${NC}"

# Dapatkan IP Publik VPS secara otomatis
echo -e "\\n${BLUE}[1/8] Mendapatkan IP Publik VPS...${NC}"
PUBLIC_IP=$(curl -s --max-time 5 https://ifconfig.me || curl -s --max-time 5 ipinfo.io/ip || echo "IP_VPS_ANDA")
echo -e "${GREEN}✔ Detected Public IP: ${PUBLIC_IP}${NC}"

# 2. Interaktif Setup config.json
echo -e "\\n${BLUE}[2/8] Setup Konfigurasi Token & Chat ID...${NC}"
read -p "Masukkan Telegram Bot Token [8773632704:AAFschVyWAyGIwGyjU5mwt1xDlMs3I-NqGc]: " TELE_TOKEN < /dev/tty
TELE_TOKEN=${TELE_TOKEN:-"8773632704:AAFschVyWAyGIwGyjU5mwt1xDlMs3I-NqGc"}

read -p "Masukkan Telegram Chat ID [298223450]: " TELE_CHAT_ID < /dev/tty
TELE_CHAT_ID=${TELE_CHAT_ID:-"298223450"}

read -p "Masukkan Dashboard URL [http://${PUBLIC_IP}:8000]: " DASH_URL < /dev/tty
DASH_URL=${DASH_URL:-"http://${PUBLIC_IP}:8000"}

read -p "Masukkan Interval Bulk Reminder (menit) [10]: " BULK_MIN < /dev/tty
BULK_MIN=${BULK_MIN:-"10"}

read -p "Masukkan Mikrotik IP/Host (Publik/VPN) [10.10.10.1]: " MK_HOST < /dev/tty
MK_HOST=${MK_HOST:-"10.10.10.1"}

read -p "Masukkan Mikrotik Username [admin]: " MK_USER < /dev/tty
MK_USER=${MK_USER:-"admin"}

read -s -p "Masukkan Mikrotik Password []: " MK_PASS < /dev/tty
echo ""
MK_PASS=${MK_PASS:-""}

# 3. Ekstrak Embedded Payload
echo -e "\\n${BLUE}[3/8] Mengekstrak file aplikasi NOC Redaman...${NC}"
cat << 'EOF' | base64 -d | tar -xzf -
{formatted_payload_placeholder}
EOF

if [ $? -eq 0 ]; then
    echo -e "${GREEN}✔ File aplikasi berhasil diekstrak.${NC}"
else
    echo -e "${RED}❌ Gagal mengekstrak file payload.${NC}"
    exit 1
fi

# Buat file config.json secara dinamis
cat <<EOF > config.json
{
  "telegram_token": "${TELE_TOKEN}",
  "telegram_chat_id": "${TELE_CHAT_ID}",
  "dashboard_url": "${DASH_URL}",
  "reminder_minutes": ${BULK_MIN},
  "mikrotik_enabled": true,
  "mikrotik_host": "${MK_HOST}",
  "mikrotik_port": 8728,
  "mikrotik_username": "${MK_USER}",
  "mikrotik_password": "${MK_PASS}",
  "mikrotik_type": "api",
  "mikrotik_use_ssl": false
}
EOF
echo -e "${GREEN}✔ config.json berhasil dibuat.${NC}"

# 4. Update Sistem & Install Paket Dasar
echo -e "\\n${BLUE}[4/8] Mengupdate sistem & menginstal Python, Pip, Virtualenv, SQLite3...${NC}"
sudo apt-get update -y
sudo apt-get install -y python3-pip python3-venv sqlite3 build-essential curl

# 5. Install Node.js v20
echo -e "\\n${BLUE}[5/8] Menginstal Node.js v20...${NC}"
if ! command -v node &> /dev/null; then
    curl -fsSL https://deb.nodesource.com/setup_20.x | sudo -E bash -
    sudo apt-get install -y nodejs
else
    echo -e "${GREEN}✔ Node.js sudah terinstal: $(node -v)${NC}"
fi

# 6. Setup Virtual Environment Python & Install Requirements
echo -e "\\n${BLUE}[6/8] Setup virtualenv Python & install library...${NC}"
python3 -m venv venv
source venv/bin/activate
pip install --upgrade pip
if [ -f "requirements.txt" ]; then
    pip install -r requirements.txt
    echo -e "${GREEN}✔ Library Python berhasil diinstal.${NC}"
else
    echo -e "${RED}⚠ requirements.txt tidak ditemukan. Menginstal manual...${NC}"
    pip install pysnmp>=4.4.12 requests>=2.25.1 pyTelegramBotAPI>=4.0.0
fi

# 6a. Auto Seed Database (Default OLTs)
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
echo -e "${GREEN}✔ Database berhasil di-seed.${NC}"

deactivate

# Setup Dependensi Node.js
echo -e "\\n${BLUE}[6b/8] Menginstal dependensi Node.js...${NC}"
npm install

# 7. Konfigurasi Firewall Lokal (Unblock Port 8000)
echo -e "\\n${BLUE}[7/8] Membuka port 8000 di firewall iptables VPS...${NC}"
# Cek apakah rule sudah ada sebelum memasukkan untuk menghindari duplikasi
sudo iptables -C INPUT -p tcp --dport 8000 -j ACCEPT &> /dev/null
if [ $? -ne 0 ]; then
    sudo iptables -I INPUT 6 -p tcp --dport 8000 -j ACCEPT
    echo -e "${GREEN}✔ Port 8000 dibuka di iptables.${NC}"
else
    echo -e "${GREEN}✔ Rule port 8000 sudah ada di iptables.${NC}"
fi

# Install iptables-persistent secara non-interaktif
echo iptables-persistent iptables-persistent/secure4 select true | sudo debconf-set-selections
echo iptables-persistent iptables-persistent/secure6 select true | sudo debconf-set-selections
sudo apt-get install -y iptables-persistent
sudo netfilter-persistent save

# 8. Daemon/Service Management (Systemd & PM2)
echo -e "\\n${BLUE}[8/8] Mendaftarkan layanan systemd & PM2...${NC}"

# Buat service collector
cat <<EOF | sudo tee /etc/systemd/system/noc-collector.service > /dev/null
[Unit]
Description=NOC Redaman Collector Daemon
After=network.target

[Service]
Type=simple
User=${CURRENT_USER}
WorkingDirectory=${INSTALL_DIR}
Environment="PYTHONIOENCODING=utf-8"
ExecStart=${INSTALL_DIR}/venv/bin/python collector.py
Restart=always
RestartSec=5

[Install]
WantedBy=multi-user.target
EOF

# Buat service telegram bot
cat <<EOF | sudo tee /etc/systemd/system/noc-telegram-bot.service > /dev/null
[Unit]
Description=NOC Redaman Telegram Bot Command Handler
After=network.target

[Service]
Type=simple
User=${CURRENT_USER}
WorkingDirectory=${INSTALL_DIR}
Environment="PYTHONIOENCODING=utf-8"
ExecStart=${INSTALL_DIR}/venv/bin/python telegram_bot.py
Restart=always
RestartSec=5

[Install]
WantedBy=multi-user.target
EOF

# Reload & Start Systemd Services
sudo systemctl daemon-reload
sudo systemctl enable noc-collector
sudo systemctl enable noc-telegram-bot
sudo systemctl restart noc-collector
sudo systemctl restart noc-telegram-bot

# PM2 Setup untuk Dashboard
if ! command -v pm2 &> /dev/null; then
    sudo npm install -g pm2
fi
pm2 delete noc-dashboard &> /dev/null || true
pm2 start server.js --name "noc-dashboard"
pm2 save

# Setup auto-start PM2 saat boot
sudo env PATH=$PATH:/usr/bin pm2 startup systemd -u ${CURRENT_USER} --hp ${USER_HOME} &> /dev/null || true
pm2 save

echo -e "\\n${GREEN}=====================================================${NC}"
echo -e "${GREEN}🎉 INSTALASI Standalone NOC Redaman Selesai! 🎉${NC}"
echo -e "${GREEN}=====================================================${NC}"
echo -e "${YELLOW}Dashboard URL : ${DASH_URL}${NC}"
echo -e "${YELLOW}Status Services:${NC}"
echo -e "  - Web Dashboard (PM2) : ${GREEN}Running${NC} (Cek dengan: pm2 status)"
echo -e "  - Collector (Systemd) : ${GREEN}Running${NC} (Cek dengan: sudo systemctl status noc-collector)"
echo -e "  - Telegram Bot (Systemd) : ${GREEN}Running${NC} (Cek dengan: sudo systemctl status noc-telegram-bot)"
echo -e "${BLUE}=====================================================${NC}"
"""
    script_content = script_content.replace("{formatted_payload_placeholder}", formatted_payload)

    # Ensure LF line endings for the shell script
    script_content = script_content.replace("\r\n", "\n")

    with open(output_installer, "w", encoding="utf-8", newline="\n") as f:
        f.write(script_content)
    
    print(f"Successfully generated standalone installer at {output_installer}")

if __name__ == "__main__":
    generate_installer()
