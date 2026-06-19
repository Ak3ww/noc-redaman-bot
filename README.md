# NOC Bot Redaman & Dashboard 🚀

Sistem pemantau redaman pelanggan (Optical Power RX) dari OLT (HSGQ & VSOL & GGCLINK). Mengirimkan notifikasi peringatan (*CRITICAL* & *WARNING*) secara real-time ke NOC via Telegram. Dilengkapi Web Dashboard interaktif.

---

## ⚡ Instalasi Sangat Mudah (Plug-and-Play)

Sistem ini didesain agar sangat ramah pemula. Anda **TIDAK PERLU** melakukan pengaturan database atau service secara manual. Cukup jalankan *Installer* otomatis yang sudah kami sediakan!

### Langkah 1: Clone Repository ke VPS
Buka terminal VPS Anda (Ubuntu/Debian), lalu jalankan perintah ini untuk mengunduh seluruh sistem:
```bash
git clone https://github.com/Ak3ww/noc-redaman-bot.git
cd noc-redaman-bot
```

### Langkah 2: Jalankan Auto Installer
Di dalam folder tersebut, jalankan file instalasi:
```bash
bash noc-bot-installer.sh
```

**Selesai!** Anda hanya tinggal duduk manis. 
Terminal akan memunculkan *Wizard* interaktif untuk meminta:
1. **Telegram Token & Chat ID**
2. **Mikrotik IP & Credentials**

Setelah Anda mengisi data tersebut, script akan bekerja otomatis 100%:
- Menginstal Python & Node.js.
- Membuat database SQLite dan mendaftarkan OLT default.
- Menjalankan `collector.py` dan `telegram_bot.py` sebagai layanan background (*Systemd*).
- Menjalankan Dashboard Web menggunakan `pm2`.
- Membuka port 8000 di Firewall iptables.

Di akhir proses, Anda akan mendapatkan URL cantik untuk mengakses Dashboard Web Anda!

---

## 🛠️ Perintah Pengendalian Layanan di VPS

Semua layanan diatur agar otomatis menyala kembali jika VPS reboot/mati lampu.

### 🖥️ Dashboard Web (PM2)
* **Cek status**: `pm2 status`
* **Restart**: `pm2 restart noc-dashboard`

### 🐍 Backend Engine (Systemd)
* **Cek status Collector**: `sudo systemctl status noc-collector`
* **Cek status Bot Telegram**: `sudo systemctl status noc-telegram-bot`
* **Cek log error**: `cat system.log`
* **Restart services**:
  ```bash
  sudo systemctl restart noc-collector
  sudo systemctl restart noc-telegram-bot
  ```
