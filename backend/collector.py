import logging
"""
collector.py — NOC Redaman: Engine Pengumpul Data SNMP & Alert
Arsitektur: Real-time poll → DB (WAL) → alerting dengan hysteresis & cooldown
"""
import sqlite3
import requests
import datetime
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
import os
import json
try:
    # pysnmp 4.x / 5.x API (lama) - digunakan jika tersedia
    from pysnmp.hlapi import (
        SnmpEngine, CommunityData, UdpTransportTarget, ContextData,
        ObjectType, ObjectIdentity, nextCmd, bulkCmd, getCmd
    )
    PYSNMP_V6 = False
except ImportError:
    # pysnmp 6.x API (baru) - fallback
    from pysnmp.hlapi.v1arch.asyncio import *
    PYSNMP_V6 = True
from mikrotik_client import get_mikrotik_data

# ── Konfigurasi ──────────────────────────────────────────────────────────────
BASE_DIR      = os.path.dirname(os.path.abspath(__file__))
DB_FILE       = os.path.join(BASE_DIR, 'redaman.db')
CFG_FILE      = os.path.join(BASE_DIR, 'config.json')
LAST_REM_FILE = os.path.join(BASE_DIR, 'last_reminder.txt')

def load_config():
    defaults = {
        "telegram_token": "8773632704:AAFschVyWAyGIwGyjU5mwt1xDlMs3I-NqGc",
        "telegram_chat_id": "298223450",
        "dashboard_url": "http://127.0.0.1:8000",
        "reminder_minutes": 60
    }
    if os.path.exists(CFG_FILE):
        try:
            with open(CFG_FILE) as f:
                data = json.load(f)
                defaults.update(data)
        except:
            pass
    return defaults

CFG           = load_config()
TELEGRAM_TOKEN   = CFG["telegram_token"]
TELEGRAM_CHAT_ID = CFG["telegram_chat_id"]
DASHBOARD_URL    = CFG["dashboard_url"]

# ── OID Vendor Map ────────────────────────────────────────────────────────────
# vsnmp: 0 = SNMPv1, 1 = SNMPv2c
OIDS = {
    "HSGQ": {
        "name":     "1.3.6.1.4.1.50224.3.12.2.1.2",
        "rx":       "1.3.6.1.4.1.50224.3.12.3.1.4",
        "tx":       "1.3.6.1.4.1.50224.3.12.3.1.3",
        "status":   "1.3.6.1.4.1.50224.3.12.2.1.3",
        "uptime":   "1.3.6.1.4.1.50224.3.12.2.1.20",
        "downtime": "1.3.6.1.4.1.50224.3.12.2.1.21",
        "offline":  "1.3.6.1.4.1.50224.3.12.2.1.22",
        "alive":    "1.3.6.1.4.1.50224.3.12.2.1.23",
        "sn":       "1.3.6.1.4.1.50224.3.12.2.1.15",
        "version":  "1.3.6.1.4.1.50224.3.12.2.1.9",
        "vsnmp":    0,
        # rx dari OLT dikirim dalam 1/100 dBm → bagi 100 (e.g. -2600 = -26.00 dBm)
        # Entry per ONU: .{onu_idx}.0.0 = AKTUAL, .{onu_idx}.65535.65535 = HISTORIS (skip)
        "rx_scale": 100.0
    },
    "VSOL": {
        "name":     "1.3.6.1.4.1.37950.1.1.6.1.1.1.1.7",
        "rx":       "1.3.6.1.4.1.37950.1.1.6.1.1.3.1.7",
        "tx":       "1.3.6.1.4.1.37950.1.1.6.1.1.3.1.6",
        "uptime":   "1.3.6.1.4.1.37950.1.1.6.1.1.1.1.8",
        "downtime": "1.3.6.1.4.1.37950.1.1.6.1.1.1.1.9",
        "offline":  "1.3.6.1.4.1.37950.1.1.6.1.1.1.1.10",
        "alive":    "1.3.6.1.4.1.37950.1.1.6.1.1.1.1.11",
        "sn":       "1.3.6.1.4.1.37950.1.1.6.1.1.2.1.5",
        "version":  "1.3.6.1.4.1.37950.1.1.6.1.1.2.1.6",
        "vsnmp":    1,
        # rx dari OLT dikirim dalam 1/10 dBm → bagi 10 (e.g. -256 = -25.6 dBm)
        "rx_scale": 10.0
    }
}

# ── DB Helpers ────────────────────────────────────────────────────────────────
def get_conn():
    """Buka koneksi SQLite dengan WAL mode dan timeout yang wajar."""
    conn = sqlite3.connect(DB_FILE, timeout=20)
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA synchronous=NORMAL")
    return conn

def ensure_schema(conn):
    """Pastikan semua tabel dan index sudah ada. Idempotent."""
    c = conn.cursor()

    # OLT registry
    c.execute('''CREATE TABLE IF NOT EXISTS olts (
        id        INTEGER PRIMARY KEY AUTOINCREMENT,
        name      TEXT NOT NULL,
        ip_port   TEXT NOT NULL,
        brand     TEXT NOT NULL,
        community TEXT DEFAULT 'public'
    )''')

    # Historical signal data (real-time, tidak di-cache)
    c.execute('''CREATE TABLE IF NOT EXISTS attenuations (
        id            INTEGER PRIMARY KEY AUTOINCREMENT,
        olt_id        INTEGER NOT NULL,
        port_name     TEXT NOT NULL,
        onu_id        TEXT NOT NULL,
        rx_power      REAL,
        tx_power      REAL,
        timestamp     DATETIME NOT NULL DEFAULT (datetime('now','localtime')),
        FOREIGN KEY(olt_id) REFERENCES olts(id)
    )''')

    # Cache untuk data STATIS / lambat berubah: nama, SN, firmware
    c.execute('''CREATE TABLE IF NOT EXISTS onu_name_cache (
        onu_id           TEXT NOT NULL,
        olt_id           INTEGER NOT NULL,
        customer_name    TEXT,
        sn               TEXT,
        firmware_version TEXT,
        last_updated     DATETIME,
        PRIMARY KEY (onu_id, olt_id),
        FOREIGN KEY(olt_id) REFERENCES olts(id)
    )''')

    # State machine alerting (satu baris per ONU per OLT)
    c.execute('''CREATE TABLE IF NOT EXISTS alert_states (
        onu_id              TEXT NOT NULL,
        olt_id              INTEGER NOT NULL,
        customer_name       TEXT,
        status              TEXT NOT NULL DEFAULT 'NORMAL',
        last_alert_time     DATETIME,
        last_offline_reason TEXT,
        last_up_time        TEXT,
        last_down_time      TEXT,
        alive_time          TEXT,
        PRIMARY KEY (onu_id, olt_id),
        FOREIGN KEY(olt_id) REFERENCES olts(id)
    )''')

    # Index untuk query cepat
    c.execute('''CREATE INDEX IF NOT EXISTS idx_att_olt_onu_ts
                 ON attenuations(olt_id, onu_id, timestamp DESC)''')

    # Tambahkan kolom pppoe_username ke tabel onu_name_cache secara dinamis jika belum ada
    try:
        c.execute("ALTER TABLE onu_name_cache ADD COLUMN pppoe_username TEXT")
        print("  [Schema] Kolom 'pppoe_username' berhasil ditambahkan ke 'onu_name_cache'.")
    except sqlite3.OperationalError:
        pass

    # Tabel daily_traffic untuk menyimpan akumulasi unduhan & unggahan harian per user
    c.execute('''CREATE TABLE IF NOT EXISTS daily_traffic (
        olt_id                INTEGER,
        onu_id                TEXT,
        pppoe_username        TEXT,
        date                  TEXT,
        download_bytes        INTEGER DEFAULT 0,
        upload_bytes          INTEGER DEFAULT 0,
        last_download_counter INTEGER DEFAULT 0,
        last_upload_counter   INTEGER DEFAULT 0,
        PRIMARY KEY (olt_id, onu_id, date)
    )''')

    # Tabel connection_events untuk mencatat riwayat disconnect secara pintar
    c.execute('''CREATE TABLE IF NOT EXISTS connection_events (
        id             INTEGER PRIMARY KEY AUTOINCREMENT,
        olt_id         INTEGER,
        onu_id         TEXT,
        customer_name  TEXT,
        pppoe_username TEXT,
        event_type     TEXT,
        reason         TEXT,
        rx_power       REAL,
        timestamp      DATETIME DEFAULT (datetime('now','localtime'))
    )''')

    # Seed default OLTs jika kosong
    c.execute("SELECT COUNT(*) FROM olts")
    if c.fetchone()[0] == 0:
        c.execute("INSERT INTO olts (name, ip_port, brand, community) VALUES ('OLT 1 (HSGQ)', '103.157.79.178:1611', 'HSGQ', 'public')")
        c.execute("INSERT INTO olts (name, ip_port, brand, community) VALUES ('OLT 2 (VSOL-GPON)', '103.157.79.178:1614', 'VSOL', 'public')")
        c.execute("INSERT INTO olts (name, ip_port, brand, community) VALUES ('OLT 3 (VSOL-1600GT)', '103.157.79.178:1615', 'VSOL', 'public')")
        print("  [Schema] Default OLTs (HSGQ, VSOL-GPON, VSOL-1600GT) seeded successfully.")

    conn.commit()

def prune_old_attenuations(conn):
    """Hapus data attenuation lebih dari 14 hari untuk menjaga performa DB."""
    c = conn.cursor()
    c.execute("""DELETE FROM attenuations
                 WHERE timestamp < datetime('now', '-14 days', 'localtime')""")
    deleted = c.rowcount
    if deleted > 0:
        print(f"  [Pruning] Hapus {deleted} baris attenuation > 14 hari.")
    conn.commit()

# ── SNMP Helpers ──────────────────────────────────────────────────────────────
SNMP_SENTINEL = (
    'No Such Object currently exists at this OID',
    'No Such Instance currently exists at this OID',
    'noSuchObject', 'noSuchInstance', ''
)

def get_snmp_walk(ip, port, community, base_oid, version=0):
    """
    SNMP Walk → dict {index: value_str}
    """
    results = {}
    
    # [PATCH] VSOL Firmware Bug Bypass:
    # Firmware VSOL melompati index secara buggy jika di-walk sekaligus.
    # Kita pecah walk menjadi per-SLOT per-PON port.
    # - V1600GT menggunakan slot 0 (index .0.pon.onu)
    # - V1600GS menggunakan slot 1 (index .1.pon.onu)
    if '37950.1.1.6.1.1' in base_oid:
        active_slots = [0, 1]  # Default fallback ke dua slot
        # Deteksi slot yang aktif (GT pakai 0, GS pakai 1) dengan 1x walk cepat
        try:
            for (errInd, errStat, errIdx, vBinds) in nextCmd(
                SnmpEngine(), CommunityData(community, mpModel=version),
                UdpTransportTarget((ip, port), timeout=5.0, retries=1),
                ContextData(), ObjectType(ObjectIdentity(base_oid)), lexicographicMode=False
            ):
                if not errInd and not errStat and vBinds:
                    oid_tuple = vBinds[0][0].asTuple()
                    base_len = len(base_oid.split('.'))
                    if len(oid_tuple) > base_len:
                        active_slots = [oid_tuple[base_len]]
                break  # Cuma butuh 1 baris pertama
        except Exception:
            pass

        for slot in active_slots:
            for pon in range(1, 17):
                pon_oid = f"{base_oid}.{slot}.{pon}"
            try:
                for (errInd, errStat, errIdx, vBinds) in nextCmd(
                    SnmpEngine(), CommunityData(community, mpModel=version),
                    UdpTransportTarget((ip, port), timeout=2.0, retries=1),
                    ContextData(), ObjectType(ObjectIdentity(pon_oid)), lexicographicMode=False
                ):
                    if errInd or errStat: break
                    for vb in vBinds:
                        val_str = vb[1].prettyPrint()
                        if val_str not in SNMP_SENTINEL:
                            parts = vb[0].prettyPrint().split('.')
                            results[f"{parts[-2]}.{parts[-1]}"] = val_str
            except Exception:
                pass
        return results

    try:
        for (errInd, errStat, errIdx, vBinds) in nextCmd(
            SnmpEngine(),
            CommunityData(community, mpModel=version),
            UdpTransportTarget((ip, port), timeout=5.0, retries=2),
            ContextData(),
            ObjectType(ObjectIdentity(base_oid)),
            lexicographicMode=False
        ):
            if errInd:
                print(f"  [SNMP Walk Error] {errInd}")
                break
            if errStat:
                print(f"  [SNMP Walk Err Status] {errStat.prettyPrint()}")
                break
            for vb in vBinds:
                oid_str = vb[0].prettyPrint()
                val_str = vb[1].prettyPrint()
                if val_str in SNMP_SENTINEL:
                    continue

                if '50224.3.12.2.1' in oid_str or '50224.3.12.3.1' in oid_str:
                    # HSGQ RX/TX: ambil hanya suffix .0.0 (nilai aktual) jika rx
                    parts = oid_str.split('.')
                    if '50224.3.12.3.1' in oid_str:
                        if len(parts) >= 3 and parts[-2] == '0' and parts[-1] == '0':
                            results[parts[-3]] = val_str
                    else:
                        # OID lain di tabel status config
                        results[parts[-1]] = val_str

                else:
                    # Default: ambil angka terakhir
                    results[oid_str.split('.')[-1]] = val_str

    except Exception as e:
        print(f"  [SNMP Walk Exception] {e}")
    return results

def get_snmp_get(ip, port, community, oid, version=0):
    """SNMP GET single OID → string atau None jika gagal/kosong."""
    try:
        errInd, errStat, errIdx, vBinds = next(
            getCmd(
                SnmpEngine(),
                CommunityData(community, mpModel=version),
                UdpTransportTarget((ip, port), timeout=5.0, retries=2),
                ContextData(),
                ObjectType(ObjectIdentity(oid))
            )
        )
        if not errInd and not errStat:
            for vb in vBinds:
                val = vb[1].prettyPrint()
                if val and val not in SNMP_SENTINEL:
                    return val
    except Exception as e:
        print(f"  [SNMP GET Error] {oid}: {e}")
    return None

# ── dBm Normalization ─────────────────────────────────────────────────────────
def normalize_dbm(raw_val, rx_scale=100.0):
    """
    Konversi nilai raw SNMP → float dBm, atau None jika tidak valid.
    Range fisik yang masuk akal untuk GPON/EPON: -5 dBm s/d -38 dBm
    """
    if raw_val is None:
        return None
    try:
        if isinstance(raw_val, str) and '.' in raw_val:
            dbm = float(raw_val)
        else:
            dbm = int(raw_val) / rx_scale
        if -38.0 <= dbm <= -5.0:
            return dbm
        return None
    except (ValueError, TypeError):
        return None

# ── Threshold & Hysteresis ────────────────────────────────────────────────────
def get_dbm_status(rx_power, current_status='NORMAL', is_currently_offline=False):
    """
    Hitung status berdasarkan rx_power & online state dengan HYSTERESIS.
    """
    if is_currently_offline:
        return 'OFFLINE'

    if rx_power is None:
        return current_status if current_status else 'NORMAL'

    if current_status == 'CRITICAL':
        if rx_power > -25.0:
            return 'WARNING'
        return 'CRITICAL'

    elif current_status == 'WARNING':
        if rx_power > -22.0:
            return 'NORMAL'
        if rx_power < -26.0:
            return 'CRITICAL'
        return 'WARNING'

    elif current_status == 'OFFLINE':
        if rx_power > -23.0:
            return 'NORMAL'
        if rx_power >= -26.0:
            return 'WARNING'
        return 'CRITICAL'

    else:  # NORMAL
        if rx_power > -23.0:
            return 'NORMAL'
        if rx_power >= -26.0:
            return 'WARNING'
        return 'CRITICAL'

def rx_bar(rx):
    """Visual indicator dBm untuk pesan Telegram."""
    if rx is None:
        return "⚫ OFFLINE"
    if rx > -23.0:
        return f"🟢 {rx:.1f} dBm"
    if rx >= -26.0:
        return f"🟡 {rx:.1f} dBm"
    return f"🔴 {rx:.1f} dBm"

def format_hsgq_alive(centiseconds_str):
    """Konversi centiseconds (TimeTicks) HSGQ ke format string hari/jam/menit/detik."""
    try:
        val = int(centiseconds_str)
        if val <= 0:
            return "00:00:00"
        seconds = val // 100
        d = seconds // 86400
        h = (seconds % 86400) // 3600
        m = (seconds % 3600) // 60
        s = seconds % 60
        if d > 0:
            return f"{d} {h:02d}:{m:02d}:{s:02d}"
        return f"{h:02d}:{m:02d}:{s:02d}"
    except Exception:
        return centiseconds_str

# ── Telegram ──────────────────────────────────────────────────────────────────
def send_telegram(message, reply_markup=None):
    url = f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/sendMessage"
    payload = {
        "chat_id": TELEGRAM_CHAT_ID,
        "text": message,
        "parse_mode": "HTML",
        "disable_web_page_preview": True
    }
    if reply_markup:
        payload["reply_markup"] = reply_markup
    try:
        r = requests.post(url, json=payload, timeout=10)
        if r.status_code != 200:
            print(f"  [Telegram Error] {r.status_code}: {r.text[:100]}")
    except Exception as e:
        print(f"  [Telegram Send Error] {e}")

# ── Cache Sync (hanya data statis: nama, SN, firmware) ───────────────────────
def sync_olt_name_cache(cursor, olt_id, ip, port, community, cfg):
    print(f"  [Cache Sync] Menyinkronkan nama/SN/firmware untuk OLT {olt_id}...")
    name_data = get_snmp_walk(ip, port, community, cfg["name"], version=cfg["vsnmp"])
    if not name_data:
        print("  [Cache Sync] Gagal walk nama. Cache lama dipertahankan.")
        return

    sn_data  = get_snmp_walk(ip, port, community, cfg["sn"],      version=cfg["vsnmp"])
    ver_data = get_snmp_walk(ip, port, community, cfg["version"],  version=cfg["vsnmp"])

    now_str = datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    for onu_idx, customer_name in name_data.items():
        sn      = sn_data.get(onu_idx, "") or ""
        fw_ver  = ver_data.get(onu_idx, "") or ""
        cursor.execute('''
            INSERT OR REPLACE INTO onu_name_cache
                (onu_id, olt_id, customer_name, sn, firmware_version, last_updated)
            VALUES (?, ?, ?, ?, ?, ?)
        ''', (onu_idx, olt_id, customer_name.strip(), sn, fw_ver, now_str))
    
    # Hapus ONU lama (stale) yang sudah tidak ada di OLT lagi (last_updated nya tertinggal)
    cursor.execute('DELETE FROM onu_name_cache WHERE olt_id = ? AND last_updated != ?', (olt_id, now_str))
    
    print(f"  [Cache Sync] {len(name_data)} ONU diperbarui (ONU lama yang terhapus otomatis dibersihkan).")

def needs_cache_sync(cursor, olt_id, max_age_sec=7200):
    row = cursor.execute(
        'SELECT COUNT(*), MAX(last_updated) FROM onu_name_cache WHERE olt_id = ?', (olt_id,)
    ).fetchone()
    count, last_sync = row
    if count == 0:
        return True
    if not last_sync:
        return True
    try:
        sync_dt = datetime.datetime.strptime(last_sync, "%Y-%m-%d %H:%M:%S")
        return (datetime.datetime.now() - sync_dt).total_seconds() > max_age_sec
    except:
        return True

# ── Alert State Machine ───────────────────────────────────────────────────────
COOLDOWN_SECS = 300  # 5 menit

def process_alert_state(cursor, olt_id, olt_name, onu_id, customer, rx_power, offline_reason=None,
                        is_currently_offline=False, last_up_time=None, last_down_time=None, alive_time=None, pppoe_username=None):
    """
    State machine yang mengelola notifikasi Telegram berdasarkan perubahan status ONU.
    """
    now     = datetime.datetime.now()
    now_str = now.strftime("%Y-%m-%d %H:%M:%S")
    bar     = rx_bar(rx_power)

    # Ambil state lama — key composite (onu_id, olt_id)
    state_row = cursor.execute(
        'SELECT status, last_alert_time, last_offline_reason, last_up_time, last_down_time, alive_time FROM alert_states WHERE onu_id = ? AND olt_id = ?',
        (onu_id, olt_id)
    ).fetchone()

    old_status = state_row[0] if state_row else None
    new_status = get_dbm_status(rx_power, current_status=old_status or 'NORMAL', is_currently_offline=is_currently_offline)

    # ── Cooldown check ────────────────────────────────────────────────────────
    secs_since = 9999
    if state_row and state_row[1]:
        try:
            last_dt = datetime.datetime.strptime(state_row[1], "%Y-%m-%d %H:%M:%S")
            secs_since = (now - last_dt).total_seconds()
        except:
            pass

    status_changed = (old_status != new_status)

    # ── Jika belum ada di DB: INSERT baru ─────────────────────────────────────
    if state_row is None:
        if new_status == 'WARNING':
            send_telegram(
                f"⚠️ <b>ALERT: REDAMAN WARNING</b> ⚠️\n{chr(8212)*28}\n"
                f"🖥 OLT    : <b>{olt_name}</b>\n"
                f"👤 Nama   : <b>{customer}</b>\n"
                f"📉 Redaman: {bar}\n"
                f"Alive Time: <b>{alive_time or '-'}</b>\n{chr(8212)*28}\n"
                f"<i>💡 Sinyal mulai memburuk, mohon dipantau.</i>",
                reply_markup={"inline_keyboard": [[{"text": "🖥 Detail (Web)", "url": f"{DASHBOARD_URL}/?onu_id={onu_id}"}]]}
            )
        elif new_status == 'CRITICAL':
            send_telegram(
                f"🚨 <b>ALERT: REDAMAN KRITIS!</b> 🚨\n{chr(8212)*28}\n"
                f"🖥 OLT    : <b>{olt_name}</b>\n"
                f"👤 Nama   : <b>{customer}</b>\n"
                f"📉 Redaman: {bar}\n"
                f"Alive Time: <b>{alive_time or '-'}</b>\n{chr(8212)*28}\n"
                f"<i>⚠️ Segera lakukan pengecekan!</i>",
                reply_markup={"inline_keyboard": [[{"text": "🖥 Detail (Web)", "url": f"{DASHBOARD_URL}/?onu_id={onu_id}"}]]}
            )
        elif new_status == 'OFFLINE':
            send_telegram(
                f"🔌 <b>ALERT: ONU OFFLINE!</b> 🔌\n{chr(8212)*28}\n"
                f"🖥 OLT    : <b>{olt_name}</b>\n"
                f"👤 Nama   : <b>{customer}</b>\n"
                f"🔍 Alasan : <b>{offline_reason or 'Unknown'}</b>\n"
                f"Last Down: <b>{last_down_time or '-'}</b>\n{chr(8212)*28}\n"
                f"<i>⚠️ Cek kelistrikan atau kabel pelanggan!</i>",
                reply_markup={"inline_keyboard": [[{"text": "🖥 Detail (Web)", "url": f"{DASHBOARD_URL}/?onu_id={onu_id}"}]]}
            )
            # Catat event disconnect awal
            cursor.execute(
                'INSERT INTO connection_events (olt_id, onu_id, customer_name, pppoe_username, event_type, reason, rx_power) VALUES (?, ?, ?, ?, ?, ?, ?)',
                (olt_id, onu_id, customer, pppoe_username, 'DISCONNECT', offline_reason or 'OLT Offline', rx_power)
            )

        cursor.execute(
            'INSERT OR IGNORE INTO alert_states (onu_id, olt_id, customer_name, status, last_alert_time, last_offline_reason, last_up_time, last_down_time, alive_time) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)',
            (onu_id, olt_id, customer, new_status, now_str, offline_reason if new_status == 'OFFLINE' else None, last_up_time, last_down_time, alive_time)
        )
        return

    # ── Status tidak berubah: update nama/reason/times, JANGAN sentuh last_alert_time ──
    if not status_changed:
        updated_reason = offline_reason if new_status == 'OFFLINE' else state_row[2]
        cursor.execute(
            'UPDATE alert_states SET customer_name = ?, last_offline_reason = ?, last_up_time = ?, last_down_time = ?, alive_time = ? WHERE onu_id = ? AND olt_id = ?',
            (customer, updated_reason, last_up_time or state_row[3], last_down_time or state_row[4], alive_time or state_row[5], onu_id, olt_id)
        )
        return

    # ── Status BERUBAH: kirim notif (dengan cooldown) + update DB ────────────
    msg = None

    if new_status == 'NORMAL':
        msg = (
            f"✅ <b>RECOVERED — KEMBALI NORMAL</b> ✅\n{chr(8212)*28}\n"
            f"🖥 OLT    : <b>{olt_name}</b>\n"
            f"👤 Nama   : <b>{customer}</b>\n"
            f"📉 Redaman: {bar}\n"
            f"Alive Time: <b>{alive_time or '-'}</b>\n"
            f"Last Up   : <b>{last_up_time or '-'}</b>\n{chr(8212)*28}\n"
            f"<i>🎉 Sinyal kembali bagus!</i>"
        )
        # Log CONNECT
        if old_status == 'OFFLINE':
            cursor.execute(
                'INSERT INTO connection_events (olt_id, onu_id, customer_name, pppoe_username, event_type, reason, rx_power) VALUES (?, ?, ?, ?, ?, ?, ?)',
                (olt_id, onu_id, customer, pppoe_username, 'CONNECT', 'ONU Online', rx_power)
            )

    elif new_status == 'OFFLINE' and old_status != 'OFFLINE':
        msg = (
            f"🔌 <b>ALERT: ONU OFFLINE!</b> 🔌\n{chr(8212)*28}\n"
            f"Status   : {old_status} ➡️ ⚫ <b>OFFLINE</b>\n"
            f"🖥 OLT    : <b>{olt_name}</b>\n"
            f"👤 Nama   : <b>{customer}</b>\n"
            f"🔍 Alasan : <b>{offline_reason or 'Unknown'}</b>\n"
            f"Last Down: <b>{last_down_time or '-'}</b>\n{chr(8212)*28}\n"
            f"<i>⚠️ Cek kelistrikan atau kabel pelanggan!</i>"
        )
        # Log DISCONNECT
        cursor.execute(
            'INSERT INTO connection_events (olt_id, onu_id, customer_name, pppoe_username, event_type, reason, rx_power) VALUES (?, ?, ?, ?, ?, ?, ?)',
            (olt_id, onu_id, customer, pppoe_username, 'DISCONNECT', offline_reason or 'OLT Offline', rx_power)
        )

    elif old_status == 'OFFLINE' and new_status != 'OFFLINE':
        msg = (
            f"✅ <b>ONU KEMBALI ONLINE</b> ✅\n{chr(8212)*28}\n"
            f"Status   : ⚫ OFFLINE ➡️ <b>{new_status}</b>\n"
            f"🖥 OLT    : <b>{olt_name}</b>\n"
            f"👤 Nama   : <b>{customer}</b>\n"
            f"📉 Redaman: {bar}\n"
            f"Alive Time: <b>{alive_time or '-'}</b>\n"
            f"Last Up   : <b>{last_up_time or '-'}</b>\n{chr(8212)*28}\n"
            f"<i>Pelanggan kembali terhubung.</i>"
        )
        # Log CONNECT
        cursor.execute(
            'INSERT INTO connection_events (olt_id, onu_id, customer_name, pppoe_username, event_type, reason, rx_power) VALUES (?, ?, ?, ?, ?, ?, ?)',
            (olt_id, onu_id, customer, pppoe_username, 'CONNECT', 'ONU Online', rx_power)
        )

    elif old_status == 'NORMAL' and new_status == 'WARNING':
        if secs_since >= COOLDOWN_SECS:
            msg = (
                f"⚠️ <b>ALERT: REDAMAN TURUN (WARNING)</b> ⚠️\n{chr(8212)*28}\n"
                f"🖥 OLT    : <b>{olt_name}</b>\n"
                f"👤 Nama   : <b>{customer}</b>\n"
                f"📉 Redaman: {bar}\n"
                f"Alive Time: <b>{alive_time or '-'}</b>\n{chr(8212)*28}\n"
                f"<i>💡 Redaman melewati -23.0 dBm</i>"
            )

    elif old_status == 'NORMAL' and new_status == 'CRITICAL':
        if secs_since >= COOLDOWN_SECS:
            msg = (
                f"🚨 <b>ALERT: REDAMAN DROP PARAH!</b> 🚨\n{chr(8212)*28}\n"
                f"🖥 OLT    : <b>{olt_name}</b>\n"
                f"👤 Nama   : <b>{customer}</b>\n"
                f"📉 Redaman: {bar}\n"
                f"Alive Time: <b>{alive_time or '-'}</b>\n{chr(8212)*28}\n"
                f"<i>⚠️ Redaman anjlok ke bawah -26.0 dBm!</i>"
            )

    elif old_status == 'WARNING' and new_status == 'CRITICAL':
        if secs_since >= COOLDOWN_SECS:
            msg = (
                f"🚨 <b>ALERT: REDAMAN SEMAKIN PARAH!</b> 🚨\n{chr(8212)*28}\n"
                f"Status   : 🟡 WARNING ➡️ 🔴 <b>CRITICAL</b>\n"
                f"🖥 OLT    : <b>{olt_name}</b>\n"
                f"👤 Nama   : <b>{customer}</b>\n"
                f"📉 Redaman: {bar}\n"
                f"Alive Time: <b>{alive_time or '-'}</b>\n{chr(8212)*28}\n"
                f"<i>⚠️ Memburuk melewati batas kritis -26.0 dBm!</i>"
            )

    elif old_status == 'CRITICAL' and new_status == 'WARNING':
        if secs_since >= COOLDOWN_SECS:
            msg = (
                f"🟡 <b>IMPROVED: SINYAL MEMBAIK</b> 🟡\n{chr(8212)*28}\n"
                f"Status   : 🔴 CRITICAL ➡️ 🟡 <b>WARNING</b>\n"
                f"🖥 OLT    : <b>{olt_name}</b>\n"
                f"👤 Nama   : <b>{customer}</b>\n"
                f"📉 Redaman: {bar}\n"
                f"Alive Time: <b>{alive_time or '-'}</b>\n{chr(8212)*28}\n"
                f"<i>👍 Perbaikan terdeteksi, belum sepenuhnya normal.</i>"
            )

    if msg:
        send_telegram(msg, reply_markup={"inline_keyboard": [[
            {"text": "🖥 Detail (Web)", "url": f"{DASHBOARD_URL}/?onu_id={onu_id}"}
        ]]})

    # Update DB
    cursor.execute('''
        UPDATE alert_states 
        SET status=?, last_alert_time=?, customer_name=?, last_offline_reason=?, last_up_time=?, last_down_time=?, alive_time=? 
        WHERE onu_id=? AND olt_id=?
    ''', (new_status, now_str, customer, offline_reason, last_up_time, last_down_time, alive_time, onu_id, olt_id))

# ── Bulk Reminder ─────────────────────────────────────────────────────────────
def check_and_send_bulk_reminder(cursor):
    cfg = load_config()
    interval_sec = cfg.get("reminder_minutes", 60) * 60

    last_ts = 0.0
    if os.path.exists(LAST_REM_FILE):
        try:
            with open(LAST_REM_FILE, 'r') as f:
                last_ts = float(f.read())
        except Exception as e:
            print(f"Error reading {LAST_REM_FILE}: {e}")
            pass

    if (time.time() - last_ts) < interval_sec:
        return

    rows = cursor.execute('''
        SELECT s.customer_name, o.name, s.status, s.last_offline_reason,
               (SELECT a.rx_power FROM attenuations a
                WHERE a.onu_id = s.onu_id AND a.olt_id = s.olt_id AND a.rx_power IS NOT NULL
                ORDER BY a.timestamp DESC LIMIT 1) as rx_power
        FROM alert_states s
        JOIN olts o ON s.olt_id = o.id
        WHERE s.status IN ('CRITICAL','WARNING')
        ORDER BY
            CASE s.status WHEN 'CRITICAL' THEN 1 ELSE 2 END,
            rx_power ASC
    ''').fetchall()

    if not rows:
        with open(LAST_REM_FILE, 'w') as f:
            f.write(str(time.time()))
        return

    total = len(rows)
    menit = interval_sec // 60
    msg  = f"🔔 <b>REMINDER — {total} ONU BERMASALAH</b>\n"
    msg += f"<i>Dikirim setiap {menit} menit</i>\n{chr(8212)*30}\n\n"

    for i, row in enumerate(rows[:15]):
        name   = (row[0] or "?")[:22]
        olt    = (row[1] or "?")[:14]
        status = row[2]
        reason = row[3]
        rx     = row[4]

        icon = {"OFFLINE": "🔌", "CRITICAL": "🔴", "WARNING": "🟡"}.get(status, "❓")
        msg += f"{i+1}. {icon} <b>{name}</b> [{olt}]\n"
        if status == 'OFFLINE':
            msg += f"   └ OFFLINE | {reason or 'Mati'}\n\n"
        else:
            msg += f"   └ {status} | {rx} dBm\n\n"

    if total > 15:
        msg += f"<i>...dan {total-15} lainnya. Buka dashboard untuk detail.</i>\n\n"

    msg += f"{chr(8212)*30}\n<i>⚠️ Mohon tim NOC segera cek!</i>"
    send_telegram(msg, reply_markup={"inline_keyboard": [
        [{"text": "🖥 Dashboard Utama", "url": f"{DASHBOARD_URL}/"}]
    ]})
    with open(LAST_REM_FILE, 'w') as f:
        f.write(str(time.time()))

# ── Main Polling Loop ─────────────────────────────────────────────────────────
def _pull_data_and_alert_impl(conn):
    print(f"\n[{datetime.datetime.now().strftime('%Y-%m-%d %H:%M:%S')}] Memulai penarikan data SNMP...")
    cursor = conn.cursor()
    ensure_schema(conn)

    # Fetch Mikrotik in a separate function to be run in executor
    def fetch_mikrotik():
        try:
            return get_mikrotik_data()
        except Exception as e:
            print(f"  [WARN] Gagal menarik data Mikrotik: {e}")
            return [], {}, {}
            
    active_users, queues_traffic, ppp_secrets = fetch_mikrotik()
        
    # Buat dictionary pencocokan untuk secret comment
    # Format comment biasanya "Pelanggan: ATIN NGATINI" -> "ATIN_NGATINI" (normalized)
    secret_map = {}
    username_to_comment = {}
    for comment, secret_name in ppp_secrets.items():
        # Buang kata pelanggan dan karakter spasi/strip agar gampang match
        clean_comment = comment.upper().replace("PELANGGAN:", "").strip()
        normalized_key = clean_comment.replace(" ", "").replace("-", "")
        secret_map[normalized_key] = secret_name
        username_to_comment[secret_name] = comment.replace("Pelanggan:", "").replace("pelanggan:", "").strip()


    # Set username aktif untuk cross-check status offline
    active_usernames = {u.get("name") for u in active_users if u.get("name")}

    olts = cursor.execute('SELECT id, name, ip_port, brand, community FROM olts').fetchall()

    for olt_id, olt_name, ip_port, olt_brand, community in olts:
        if olt_brand not in OIDS:
            print(f"  Skip {olt_name}: Brand '{olt_brand}' tidak dikenal.")
            continue

        cfg = OIDS.get(olt_brand, {})

        ip, port = ip_port.rsplit(':', 1)
        port = int(port)

        status_data = {}
        rx_data = {}
        up_time_data = {}
        down_time_data = {}
        offline_data = {}
        alive_data = {}
        
        cfg = OIDS[olt_brand]
        if not cfg.get("rx"):
            print(f"  Skip {olt_name}: OID rx belum diset.")
            continue

        # ── Sync cache nama/SN/firmware (hanya jika perlu) ───────────────────
        if needs_cache_sync(cursor, olt_id):
            sync_olt_name_cache(cursor, olt_id, ip, port, community, cfg)
            conn.commit()

        # ── Tarik rx_power LANGSUNG dari OLT (real-time, bukan cache) ────────
        rx_data = get_snmp_walk(ip, port, community, cfg["rx"], version=cfg["vsnmp"])
        
        # Tarik data status, registrasi, alive time, dan alasan offline
        up_time_data = get_snmp_walk(ip, port, community, cfg["uptime"], version=cfg["vsnmp"])
        down_time_data = get_snmp_walk(ip, port, community, cfg["downtime"], version=cfg["vsnmp"])
        offline_data = get_snmp_walk(ip, port, community, cfg["offline"], version=cfg["vsnmp"])
        alive_data = get_snmp_walk(ip, port, community, cfg["alive"], version=cfg["vsnmp"])
        
        if olt_brand == "HSGQ":
            status_data = get_snmp_walk(ip, port, community, cfg["status"], version=cfg["vsnmp"])
        else:
            status_data = {}
            
        # Hapus ONU yang sudah tidak ada di OLT dari cache (Stale Cache Invalidation)
        # DINONAKTIFKAN: Mencegah spam OFFLINE untuk ONU yang tidak terdeteksi sementara
        current_snmp_ids = set()
        for d in [rx_data, up_time_data, down_time_data, offline_data, alive_data, status_data]:
            if d:
                current_snmp_ids.update(d.keys())

        # Jika data fundamental (uptime / rx) gagal ditarik sama sekali, skip OLT ini di siklus ini
        if not rx_data and not up_time_data:
            print(f"  [WARN] Gagal menarik data dari {olt_name}. Siklus ini dilewati.")
            continue

        print(f"  [{olt_name}] Data diterima dari SNMP.")

        # ── Ambil semua ONU dari cache sebagai daftar master ─────────────────
        cached = cursor.execute(
            'SELECT onu_id, customer_name FROM onu_name_cache WHERE olt_id = ?', (olt_id,)
        ).fetchall()
        all_onus = {r[0]: r[1] for r in cached}

        # Gabungkan keys dari semua data SNMP yang berhasil di-pull
        keys_to_process = current_snmp_ids

        # Jika ada ONU baru yang belum masuk cache, paksa sinkronisasi cache!
        missing_onus = [k for k in keys_to_process if k not in all_onus]
        if missing_onus:
            print(f"  [Cache Sync] Ditemukan {len(missing_onus)} ONU baru di {olt_name}. Memaksa sinkronisasi nama...")
            try:
                sync_olt_name_cache(cursor, olt_id, ip, port, community, cfg)
                conn.commit()
                # Reload cache
                cached = cursor.execute('SELECT onu_id, customer_name FROM onu_name_cache WHERE olt_id = ?', (olt_id,)).fetchall()
                all_onus = {r[0]: r[1] for r in cached}
            except Exception as e:
                print(f"  [Error] Gagal sinkronisasi nama ONU baru: {e}")

        # ONU baru yang belum di cache — GET langsung
        for onu_idx in keys_to_process:
            if onu_idx not in all_onus:
                name_val = get_snmp_get(ip, port, community, f"{cfg['name']}.{onu_idx}", version=cfg["vsnmp"])
                sn_val   = get_snmp_get(ip, port, community, f"{cfg['sn']}.{onu_idx}",   version=cfg["vsnmp"])
                ver_val  = get_snmp_get(ip, port, community, f"{cfg['version']}.{onu_idx}", version=cfg["vsnmp"])
                customer = (name_val or f"ONU-{onu_idx}").strip()
                cursor.execute(
                    'INSERT OR REPLACE INTO onu_name_cache (onu_id, olt_id, customer_name, sn, firmware_version, last_updated) VALUES (?,?,?,?,?,?)',
                    (onu_idx, olt_id, customer, sn_val or "", ver_val or "", datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S"))
                )
                all_onus[onu_idx] = customer

        # ── Proses setiap ONU ─────────────────────────────────────────────────
        for onu_idx, customer in all_onus.items():
            try:
                raw_rx = rx_data.get(onu_idx)
                dbm    = normalize_dbm(raw_rx, rx_scale=cfg.get("rx_scale", 100.0))

                # Ambil metrik diagnostik
                last_up = up_time_data.get(onu_idx)
                last_down = down_time_data.get(onu_idx)
                alive = alive_data.get(onu_idx)
                reason = offline_data.get(onu_idx)

                # Bersihkan string atau berikan default
                if last_up is not None: last_up = str(last_up).strip()
                if last_down is not None: last_down = str(last_down).strip()
                if alive is not None: alive = str(alive).strip()
                if reason is not None: reason = str(reason).strip()

                # ── Mikrotik Mapping ─────────────────────────────
                # Ambil atau buat pppoe_username
                row_cache = cursor.execute(
                    'SELECT pppoe_username FROM onu_name_cache WHERE onu_id = ? AND olt_id = ?',
                    (onu_idx, olt_id)
                ).fetchone()
                pppoe_user = row_cache[0] if row_cache else None

                # Auto-mapping via Secret Comment (selalu sinkronisasi jika ada comment yang cocok)
                clean_customer = (customer or "").upper().strip()
                normalized_customer = clean_customer.replace(" ", "").replace("-", "")
                matched_secret = secret_map.get(normalized_customer)
                if matched_secret and pppoe_user != matched_secret:
                    pppoe_user = matched_secret
                    cursor.execute(
                        'UPDATE onu_name_cache SET pppoe_username = ? WHERE onu_id = ? AND olt_id = ?',
                        (pppoe_user, onu_idx, olt_id)
                    )
                    print(f"  [Auto-Map] ONU {onu_idx} ({customer}) -> PPPoE {pppoe_user}")
                elif not pppoe_user:
                    # Fallback normalisasi (jika belum ada comment)
                    clean_cust = "".join([c.lower() for c in (customer or "") if c.isalnum() or c == " "]).replace(" ", "_")
                    if clean_cust:
                        pppoe_user = clean_cust
                        cursor.execute(
                            'UPDATE onu_name_cache SET pppoe_username = ? WHERE onu_id = ? AND olt_id = ?',
                            (pppoe_user, onu_idx, olt_id)
                        )

                # Mikrotik Source of Truth: update nama ONU dari Secret Comment
                if pppoe_user:
                    mik_name = username_to_comment.get(pppoe_user)
                    if mik_name and mik_name != customer:
                        print(f"  [Mikrotik Sync] Rename ONU {onu_idx}: '{customer}' -> '{mik_name}'")
                        customer = mik_name
                        cursor.execute(
                            'UPDATE onu_name_cache SET customer_name = ? WHERE onu_id = ? AND olt_id = ?',
                            (customer, onu_idx, olt_id)
                        )
                        all_onus[onu_idx] = customer

                # Tentukan online/offline status secara akurat (berdasarkan OLT)
                is_currently_offline = False
                
                # Fungsi helper untuk fallback ke status database lama jika data OLT kosong (Packet Loss)
                def get_previous_offline_state():
                    state_row = cursor.execute('SELECT status FROM alert_states WHERE onu_id = ? AND olt_id = ?', (onu_idx, olt_id)).fetchone()
                    return (state_row[0] == 'OFFLINE') if state_row else False

                if olt_brand == "HSGQ":
                    status_val = status_data.get(onu_idx)
                    if status_val is not None:
                        is_currently_offline = (str(status_val).strip() != '1')
                    else:
                        if dbm is not None:
                            is_currently_offline = False
                        else:
                            is_currently_offline = get_previous_offline_state()
                    
                    if alive and alive != "0":
                        alive = format_hsgq_alive(alive)
                    else:
                        alive = "-"

                elif olt_brand == "VSOL":
                    valid_up = last_up and str(last_up) not in ("N/A", "0000-00-00 00:00:00", "")
                    valid_down = last_down and str(last_down) not in ("N/A", "0000-00-00 00:00:00", "")
                    
                    if valid_up and valid_down:
                        # Jika kedua timestamp valid, percayai timestamp OLT (Paling Akurat)
                        is_currently_offline = (str(last_down) > str(last_up))
                    else:
                        # Jika timestamp tidak lengkap, cek dbm
                        if dbm is not None:
                            is_currently_offline = False
                        else:
                            is_currently_offline = get_previous_offline_state()

                # ── Cross-Check Mikrotik (Anti-False Offline) ───────────────────
                # Dihapus karena Hysteresis sudah cukup kuat dan user meminta status OLT menjadi sumber kebenaran (Source of Truth)
                # agar Dashboard sinkron 100% dengan OLT.

                # Jika offline, gunakan reason OID. Jika online, set reason ke None atau simpan historis
                if is_currently_offline:
                    offline_reason = reason or "Unknown"
                    dbm = None
                else:
                    offline_reason = None

                # Proses data traffic harian jika Mikrotik aktif
                if pppoe_user and pppoe_user in queues_traffic:
                    q_data = queues_traffic[pppoe_user]
                    up_counter = q_data["upload_bytes"]
                    down_counter = q_data["download_bytes"]
                    today_date = datetime.date.today().strftime("%Y-%m-%d")

                    dt_row = cursor.execute(
                        'SELECT download_bytes, upload_bytes, last_download_counter, last_upload_counter FROM daily_traffic WHERE olt_id = ? AND onu_id = ? AND date = ?',
                        (olt_id, onu_idx, today_date)
                    ).fetchone()

                    if dt_row is None:
                        # Buat baris harian baru
                        cursor.execute(
                            'INSERT INTO daily_traffic (olt_id, onu_id, pppoe_username, date, download_bytes, upload_bytes, last_download_counter, last_upload_counter) VALUES (?, ?, ?, ?, 0, 0, ?, ?)',
                            (olt_id, onu_idx, pppoe_user, today_date, down_counter, up_counter)
                        )
                    else:
                        db_down, db_up, last_down_c, last_up_c = dt_row
                        
                        # Hitung delta
                        delta_down = down_counter - last_down_c
                        if delta_down < 0:
                            delta_down = down_counter
                        delta_up = up_counter - last_up_c
                        if delta_up < 0:
                            delta_up = up_counter

                        new_down = db_down + delta_down
                        new_up = db_up + delta_up

                        cursor.execute(
                            'UPDATE daily_traffic SET download_bytes = ?, upload_bytes = ?, last_download_counter = ?, last_upload_counter = ? WHERE olt_id = ? AND onu_id = ? AND date = ?',
                            (new_down, new_up, down_counter, up_counter, olt_id, onu_idx, today_date)
                        )

                # Simpan ke history attenuations (data real-time, BUKAN cache)
                cursor.execute(
                    'INSERT INTO attenuations (olt_id, port_name, onu_id, rx_power, timestamp) VALUES (?,?,?,?,datetime("now","localtime"))',
                    (olt_id, "GPON", onu_idx, dbm)
                )

                # Proses alerting
                process_alert_state(
                    cursor=cursor,
                    olt_id=olt_id,
                    olt_name=olt_name,
                    onu_id=onu_idx,
                    customer=customer,
                    rx_power=dbm,
                    offline_reason=offline_reason,
                    is_currently_offline=is_currently_offline,
                    last_up_time=last_up,
                    last_down_time=last_down,
                    alive_time=alive,
                    pppoe_username=pppoe_user
                )

            except Exception as e:
                import traceback
                logging.error(f"  [Error] ONU {onu_idx}: {e}", exc_info=True)
                traceback.print_exc()

        conn.commit()

    # ── Bulk reminder ─────────────────────────────────────────────────────────
    check_and_send_bulk_reminder(cursor)

    # ── Pruning data lama (1x per hari cukup, tapi cek tiap siklus — cepat) ──
    prune_old_attenuations(conn)

    print(f"  Selesai.\n")

# ── Entry Point ───────────────────────────────────────────────────────────────

def pull_data_and_alert():
    conn = get_conn()
    try:
        _pull_data_and_alert_impl(conn)
    finally:
        conn.close()
logging.basicConfig(filename='system.log', level=logging.ERROR, format='%(asctime)s - %(name)s - %(levelname)s - %(message)s')

if __name__ == "__main__":
    print("=" * 50)
    print("  Collector NOC Redaman — Siap")
    print("=" * 50)
    while True:
        try:
            pull_data_and_alert()
        except Exception as e:
            logging.error(f"[FATAL] Error loop utama: {e}", exc_info=True)
        time.sleep(30)
