"""
mikrotik_client.py — NOC Redaman: Modul Klien Mikrotik RouterOS
Mendukung REST API (v7), API Socket Murni (v6/v7), dan Fallback Simulasi (Mock Data)
"""
import socket
import ssl
import json
import os
import requests
import sqlite3

# ── Load Config ──────────────────────────────────────────────────────────────
BASE_DIR = os.path.dirname(os.path.abspath(__file__))
CFG_FILE = os.path.join(BASE_DIR, 'config.json')
DB_FILE  = os.path.join(BASE_DIR, 'redaman.db')

def load_config():
    if os.path.exists(CFG_FILE):
        try:
            with open(CFG_FILE) as f:
                return json.load(f)
        except:
            pass
    return {}

# ── RouterOS Socket API Helper ────────────────────────────────────────────────
class RouterOSApi:
    def __init__(self, host, port, username, password, use_ssl=False):
        self.host = host
        self.port = port
        self.username = username
        self.password = password
        self.use_ssl = use_ssl
        self.sock = None

    def connect(self):
        # 1. Coba Plaintext Login Terlebih Dahulu (Standard untuk RouterOS v7)
        s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        s.settimeout(5)
        if self.use_ssl:
            context = ssl.create_default_context()
            context.check_hostname = False
            context.verify_mode = ssl.CERT_NONE
            self.sock = context.wrap_socket(s, server_hostname=self.host)
        else:
            self.sock = s
        self.sock.connect((self.host, self.port))
        
        try:
            self.write_word("/login")
            self.write_word("=name=" + self.username)
            self.write_word("=password=" + self.password)
            self.write_word("") # Selesaikan kalimat
            
            res = self.read_sentence()
            is_trap = False
            for w in res:
                if "!trap" in w:
                    is_trap = True
            
            if not is_trap:
                return # Plaintext login sukses!
        except Exception as e:
            pass

        # 2. Jika Gagal, Reconnect & Coba Legacy MD5 Challenge Login (Standard untuk RouterOS v6)
        if self.sock:
            self.sock.close()
            
        s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        s.settimeout(5)
        if self.use_ssl:
            context = ssl.create_default_context()
            context.check_hostname = False
            context.verify_mode = ssl.CERT_NONE
            self.sock = context.wrap_socket(s, server_hostname=self.host)
        else:
            self.sock = s
        self.sock.connect((self.host, self.port))
        
        self.write_word("/login")
        self.write_word("") # Selesaikan kalimat
        res = self.read_sentence()
        
        ret_val = None
        for word in res:
            if word.startswith("=ret="):
                ret_val = word[5:]
        
        if ret_val:
            import hashlib
            chal = bytes.fromhex(ret_val)
            md = hashlib.md5()
            md.update(b"\x00")
            md.update(self.password.encode('utf-8'))
            md.update(chal)
            hashed_pass = md.hexdigest()
            
            self.write_word("/login")
            self.write_word("=name=" + self.username)
            self.write_word("=response=00" + hashed_pass)
            self.write_word("") # Selesaikan kalimat
            res2 = self.read_sentence()
            for w in res2:
                if "!trap" in w:
                    raise Exception("Login failed: " + str(res2))
        else:
            raise Exception("Login failed: Router did not return challenge nor accept plaintext")

    def write_len(self, length):
        if length < 0x80:
            self.sock.send(bytes([length]))
        elif length < 0x4000:
            length |= 0x8000
            self.sock.send(bytes([length >> 8, length & 0xFF]))
        elif length < 0x200000:
            length |= 0xC00000
            self.sock.send(bytes([length >> 16, (length >> 8) & 0xFF, length & 0xFF]))
        else:
            raise Exception("Word too long")

    def read_len(self):
        b1 = self.sock.recv(1)[0]
        if (b1 & 0x80) == 0x00:
            return b1
        elif (b1 & 0xC0) == 0x80:
            b2 = self.sock.recv(1)[0]
            return ((b1 & 0x3F) << 8) + b2
        elif (b1 & 0xE0) == 0xC0:
            b2 = self.sock.recv(1)[0]
            b3 = self.sock.recv(1)[0]
            return ((b1 & 0x1F) << 16) + (b2 << 8) + b3
        raise Exception("Byte length not supported")

    def write_word(self, word):
        b = word.encode('utf-8')
        self.write_len(len(b))
        self.sock.send(b)

    def read_word(self):
        length = self.read_len()
        if length == 0:
            return ""
        b = b""
        while len(b) < length:
            b += self.sock.recv(length - len(b))
        return b.decode('utf-8')

    def read_sentence(self):
        sentence = []
        while True:
            w = self.read_word()
            if not w:
                break
            sentence.append(w)
        return sentence

    def talk(self, words):
        for w in words:
            self.write_word(w)
        self.write_word("") # empty word finishes request
        
        replies = []
        while True:
            sentence = self.read_sentence()
            replies.append(sentence)
            if sentence[0] == "!done":
                break
        return replies

    def close(self):
        if self.sock:
            try:
                self.sock.close()
            except:
                pass
            self.sock = None

# ── Mock Data Generator (Fallback) ───────────────────────────────────────────
def get_mock_active_pppoe():
    """Mengenerasi data PPPoE palsu berdasarkan nama ONU yang ada di SQLite."""
    sessions = []
    if not os.path.exists(DB_FILE):
        return sessions
    try:
        conn = sqlite3.connect(DB_FILE)
        cursor = conn.cursor()
        # Cari semua ONU di name cache
        rows = cursor.execute('SELECT onu_id, customer_name, olt_id FROM onu_name_cache').fetchall()
        conn.close()
        
        for idx, (onu_id, name, olt_id) in enumerate(rows):
            # Normalisasi nama menjadi pppoe_username
            clean_name = "".join([c.lower() for c in (name or "") if c.isalnum() or c == " "]).replace(" ", "_")
            if not clean_name:
                clean_name = f"user_{onu_id.replace(':', '_').replace('/', '_')}"
            
            # Buat IP palsu yang konsisten
            ip_last = hash(clean_name) % 250 + 2
            ip = f"10.50.{olt_id}.{ip_last}"
            
            # Buat MAC palsu
            mac = f"00:1A:11:22:33:{idx:02X}"
            
            # Uptime acak
            uptime = f"{1 + (idx % 24)}h {(idx * 7) % 60}m"
            
            sessions.append({
                "name": clean_name,
                "onu_id": onu_id,
                "olt_id": olt_id,
                "address": ip,
                "uptime": uptime,
                "mac-address": mac
            })
    except Exception as e:
        print(f"[Mikrotik Mock] Error: {e}")
    return sessions

def get_mock_queues_traffic():
    """Mengenerasi data Simple Queue palsu dengan traffic byte."""
    queues = {}
    sessions = get_mock_active_pppoe()
    for idx, s in enumerate(sessions):
        # Hitung download/upload rate (live)
        # 10% kemungkinan sedang idle, sisanya aktif download/upload
        is_idle = (idx % 10) == 0
        tx_rate = 0 if is_idle else ((idx * 2317) % 15000000) # up to 15 Mbps
        rx_rate = 0 if is_idle else ((idx * 8371) % 45000000) # up to 45 Mbps
        
        # Byte total kumulatif (misal 5 GB s/d 120 GB)
        tx_bytes = 1024 * 1024 * 1024 * (5 + (idx % 50)) # upload
        rx_bytes = 1024 * 1024 * 1024 * (12 + (idx % 110)) # download
        
        queues[s["name"]] = {
            "name": s["name"],
            "upload_rate": tx_rate,
            "download_rate": rx_rate,
            "upload_bytes": tx_bytes,
            "download_bytes": rx_bytes
        }
    return queues

def get_mock_ppp_secrets():
    """Mengenerasi data mock secret PPPoE kosong."""
    return {}

# ── API Utama Modul ───────────────────────────────────────────────────────────
_persistent_api = None
_rest_session = None
_cache = {"time": 0, "active": [], "queues": {}, "secrets": {}}
import time

def get_mikrotik_data():
    """
    Fungsi utama untuk mengambil data active PPPoE dan simple queues.
    Menggunakan persistent connection untuk mengurangi spam log di Mikrotik.
    """
    global _rest_session, _persistent_api
    cfg = load_config()
    enabled = cfg.get("mikrotik_enabled", False)
    host = cfg.get("mikrotik_host", "")
    port = cfg.get("mikrotik_port", 8728)
    username = cfg.get("mikrotik_username", "")
    password = cfg.get("mikrotik_password", "")
    m_type = cfg.get("mikrotik_type", "api")
    use_ssl = cfg.get("mikrotik_use_ssl", False)

    if not enabled or not host or not username:
        return get_mock_active_pppoe(), get_mock_queues_traffic(), get_mock_ppp_secrets()

    # Use cache if less than 1 hour (3600 seconds) old to prevent login spam
    if time.time() - _cache["time"] < 3600:
        return _cache["active"], _cache["queues"], _cache["secrets"]

    try:
        active_users, queues_traffic, ppp_secrets = [], {}, {}

        if m_type == "rest":
            if _rest_session is None:
                _rest_session = requests.Session()
                _rest_session.auth = (username, password)
            
            proto = "https" if use_ssl else "http"
            url = f"{proto}://{host}:{port}/rest"
            
            # Health check simple
            try:
                r1 = _rest_session.get(f"{url}/ppp/active", verify=False, timeout=5)
                r1.raise_for_status()
                for item in r1.json():
                    active_users.append({"name": item.get("name"), "address": item.get("address"), "uptime": item.get("uptime"), "mac-address": item.get("mac-address")})
                
                r2 = _rest_session.get(f"{url}/queue/simple", verify=False, timeout=5)
                for item in r2.json():
                    q_name = item.get("name", "")
                    if q_name.startswith("<pppoe-") and q_name.endswith(">"): q_name = q_name[7:-1]
                    bytes_str = item.get("bytes", "0/0").split("/"); rate_str = item.get("rate", "0/0").split("/")
                    queues_traffic[q_name] = {"name": q_name, "upload_bytes": int(bytes_str[0]), "download_bytes": int(bytes_str[1]), "upload_rate": int(rate_str[0]), "download_rate": int(rate_str[1])}
                
                r3 = _rest_session.get(f"{url}/ppp/secret", verify=False, timeout=5)
                for item in r3.json():
                    if item.get("comment") and item.get("name"):
                        import re
                        cmt = re.sub(r'\s*-\s*\d+$', '', item["comment"]).strip()
                        ppp_secrets[cmt] = item["name"]
            except Exception:
                _rest_session = None
                raise
        else:
            if _persistent_api is None or _persistent_api.sock is None:
                _persistent_api = RouterOSApi(host, port, username, password, use_ssl)
                _persistent_api.connect()
            
            try:
                active_reply = _persistent_api.talk(["/ppp/active/print"])
                for sentence in active_reply:
                    if sentence[0] == "!re":
                        item = {w.split("=", 2)[1]: w.split("=", 2)[2] for w in sentence[1:] if w.startswith("=")}
                        active_users.append({"name": item.get("name"), "address": item.get("address"), "uptime": item.get("uptime"), "mac-address": item.get("mac-address")})
                
                queues_reply = _persistent_api.talk(["/queue/simple/print"])
                for sentence in queues_reply:
                    if sentence[0] == "!re":
                        item = {w.split("=", 2)[1]: w.split("=", 2)[2] for w in sentence[1:] if w.startswith("=")}
                        q_name = item.get("name", "")
                        if q_name.startswith("<pppoe-") and q_name.endswith(">"): q_name = q_name[7:-1]
                        b, r = item.get("bytes", "0/0").split("/"), item.get("rate", "0/0").split("/")
                        queues_traffic[q_name] = {"name": q_name, "upload_bytes": int(b[0]), "download_bytes": int(b[1]), "upload_rate": int(r[0]), "download_rate": int(r[1])}
                
                secrets_reply = _persistent_api.talk(["/ppp/secret/print"])
                for sentence in secrets_reply:
                    if sentence[0] == "!re":
                        item = {w.split("=", 2)[1]: w.split("=", 2)[2] for w in sentence[1:] if w.startswith("=")}
                        if item.get("comment") and item.get("name"):
                            import re
                            cmt = re.sub(r'\s*-\s*\d+$', '', item["comment"]).strip()
                            ppp_secrets[cmt] = item["name"]
            except Exception:
                _persistent_api.close()
                _persistent_api = None
                raise
        
        _cache["time"] = time.time()
        _cache["active"] = active_users
        _cache["queues"] = queues_traffic
        _cache["secrets"] = ppp_secrets
        
        return active_users, queues_traffic, ppp_secrets
    except Exception as e:
        print(f"[Mikrotik API] Error: {e}. Falling back to mock.")
        return get_mock_active_pppoe(), get_mock_queues_traffic(), get_mock_ppp_secrets()

if __name__ == "__main__":
    # Test file
    print("Menjalankan pengetesan modul mikrotik_client...")
    users, queues = get_mikrotik_data()
    print(f"Berhasil menarik {len(users)} user PPPoE aktif.")
    if users:
        print(f"Sampel User: {users[0]}")
        print(f"Sampel Queue: {queues.get(users[0]['name'])}")
