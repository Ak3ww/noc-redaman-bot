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
    "mikrotik_client.py",
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

# 2. Setup Konfigurasi Token & Chat ID secara Otomatis
echo -e "\\n${BLUE}[2/9] Setup Konfigurasi Token, Mikrotik & Chat ID...${NC}"

TELE_TOKEN="8773632704:AAFschVyWAyGIwGyjU5mwt1xDlMs3I-NqGc"
TELE_CHAT_ID="298223450"
DASH_DOMAIN="noc.euginemediagroup.com"
DASH_URL="https://${DASH_DOMAIN}"
BULK_MIN="10"
MK_HOST="103.157.79.178"
MK_USER="billinghub.id"
MK_PASS="@eugine0909@"

echo -e "${GREEN}✔ Semua konfigurasi berhasil di-set secara otomatis.${NC}"

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
  "mikrotik_port": 8520,
  "mikrotik_username": "${MK_USER}",
  "mikrotik_password": "${MK_PASS}",
  "mikrotik_type": "api",
  "mikrotik_use_ssl": false
}
EOF
echo -e "${GREEN}✔ config.json berhasil dibuat.${NC}"

# 4. Update Sistem, Swap File, & Install Paket Dasar
echo -e "\\n${BLUE}[1/8] Setup 4GB Swap File & Instalasi dependensi sistem (Python3, Node.js, Docker)...${NC}"

# Setup 4GB Swap
if free | awk '/^Swap:/ {exit !$2}'; then
    echo -e "${GREEN}✔ Swap file sudah ada.${NC}"
else
    echo -e "${YELLOW}Membuat 4GB Swap file agar VPS 2GB tidak crash...${NC}"
    sudo fallocate -l 4G /swapfile || sudo dd if=/dev/zero of=/swapfile bs=1M count=4096
    sudo chmod 600 /swapfile
    sudo mkswap /swapfile
    sudo swapon /swapfile
    echo '/swapfile none swap sw 0 0' | sudo tee -a /etc/fstab
    echo -e "${GREEN}✔ Swap file 4GB berhasil dibuat.${NC}"
fi

sudo apt-get update -y
sudo apt-get install -y python3 python3-pip python3-venv sqlite3 curl docker.io docker-compose

# Pastikan Node.js 20.x terpasang
curl -fsSL https://deb.nodesource.com/setup_20.x | sudo -E bash -
sudo apt-get install -y nodejs

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
    pip install pyasn1==0.4.8 pysnmp==4.4.12 requests>=2.25.1 pyTelegramBotAPI>=4.0.0
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
        ('HSGQ-G02ID', '103.157.79.178:1611', 'HSGQ', 'public'),
        ('VSOL-GPON', '192.168.30.6:161', 'VSOL', 'public'),
        ('VSOL-1600GT', '192.168.30.7:1615', 'VSOL', 'public')
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

# 8.5. Setup LibreNMS (Docker Compose)
echo -e "\\n${BLUE}[8.5/9] Setup LibreNMS via Docker Compose...${NC}"
mkdir -p /opt/librenms
cd /opt/librenms

# Buat docker-compose.yml untuk LibreNMS
cat <<EOF_DOCKER > docker-compose.yml
version: "3.5"

services:
  db:
    image: mariadb:10.5
    container_name: librenms_db
    command:
      - "mysqld"
      - "--innodb-file-per-table=1"
      - "--lower-case-table-names=0"
      - "--character-set-server=utf8mb4"
      - "--collation-server=utf8mb4_unicode_ci"
    volumes:
      - ./db:/var/lib/mysql
    environment:
      - TZ=Asia/Jakarta
      - MYSQL_ALLOW_EMPTY_PASSWORD=yes
      - MYSQL_DATABASE=librenms
      - MYSQL_USER=librenms
      - MYSQL_PASSWORD=librenmspass
    restart: always

  redis:
    image: redis:5.0-alpine
    container_name: librenms_redis
    environment:
      - TZ=Asia/Jakarta
    restart: always

  librenms:
    image: librenms/librenms:latest
    container_name: librenms
    hostname: librenms
    cap_add:
      - NET_ADMIN
      - NET_RAW
    ports:
      - target: 8000
        published: 8080
        protocol: tcp
    depends_on:
      - db
      - redis
    volumes:
      - ./data:/data
    environment:
      - TZ=Asia/Jakarta
      - PUID=1000
      - PGID=1000
      - DB_HOST=db
      - DB_NAME=librenms
      - DB_USER=librenms
      - DB_PASSWORD=librenmspass
      - DB_TIMEOUT=60
    restart: always

  dispatcher:
    image: librenms/librenms:latest
    container_name: librenms_dispatcher
    hostname: librenms-dispatcher
    cap_add:
      - NET_ADMIN
      - NET_RAW
    depends_on:
      - librenms
      - db
      - redis
    volumes:
      - ./data:/data
    environment:
      - TZ=Asia/Jakarta
      - PUID=1000
      - PGID=1000
      - DB_HOST=db
      - DB_NAME=librenms
      - DB_USER=librenms
      - DB_PASSWORD=librenmspass
      - DB_TIMEOUT=60
      - DISPATCHER_NODE_ID=dispatcher1
      - SIDECAR_DISPATCHER=1
    restart: always
EOF_DOCKER

echo -e "${YELLOW}Menjalankan LibreNMS Docker (Ini akan memakan waktu untuk pull image)...${NC}"
sudo docker-compose up -d || sudo docker compose up -d
echo -e "${GREEN}✔ LibreNMS berhasil dijalankan di port 8080.${NC}"

# Kembalikan posisi direktori kerja ke folder bot
cd ${INSTALL_DIR}

# 9. Setup Nginx & Auto Let's Encrypt SSL
echo -e "\\n${BLUE}[9/9] Setup Nginx Reverse Proxy & SSL (Let's Encrypt)...${NC}"
sudo apt-get install -y nginx certbot python3-certbot-nginx

# Buat konfigurasi Nginx
cat <<EOF_NGINX | sudo tee /etc/nginx/sites-available/${DASH_DOMAIN} > /dev/null
server {
    listen 80;
    server_name ${DASH_DOMAIN};
    location / {
        proxy_pass http://127.0.0.1:8000;
        proxy_set_header Host \\$host;
        proxy_set_header X-Real-IP \\$remote_addr;
        proxy_set_header X-Forwarded-For \\$proxy_add_x_forwarded_for;
        proxy_set_header X-Forwarded-Proto \\$scheme;
    }
}
EOF_NGINX

# Aktifkan site
sudo rm -f /etc/nginx/sites-enabled/default
sudo ln -sf /etc/nginx/sites-available/${DASH_DOMAIN} /etc/nginx/sites-enabled/
sudo systemctl restart nginx

# Install SSL Let's Encrypt
echo -e "\\n${YELLOW}Sedang memproses SSL Let's Encrypt untuk ${DASH_DOMAIN}...${NC}"
sudo certbot --nginx -d ${DASH_DOMAIN} --non-interactive --agree-tos --register-unsafely-without-email || true
echo -e "${GREEN}✔ Setup Nginx dan SSL selesai.${NC}"

echo -e "\\n${GREEN}=====================================================${NC}"
echo -e "${GREEN}🎉 INSTALASI Standalone NOC Redaman Selesai! 🎉${NC}"
echo -e "${GREEN}=====================================================${NC}"
echo -e "${YELLOW}Dashboard URL : ${DASH_URL}${NC}"
echo -e "${YELLOW}Status Services:${NC}"
echo -e "  - Web Dashboard (PM2) : ${GREEN}Running${NC} (Cek dengan: pm2 status)"
echo -e "  - Collector (Systemd) : ${GREEN}Running${NC} (Cek dengan: sudo systemctl status noc-collector)"
echo -e "  - Telegram Bot (Systemd) : ${GREEN}Running${NC} (Cek dengan: sudo systemctl status noc-telegram-bot)"
echo -e "  - LibreNMS (Docker) : ${GREEN}Running${NC} (Akses via IP_VPS:8080 - username admin/admin)"
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
