import sys
import os

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
sys.path.append(BASE_DIR)
from mikrotik_client import get_mikrotik_data, load_config

cfg = load_config()
print(f"Config: {cfg.get('mikrotik_host')} | User: {cfg.get('mikrotik_username')} | Type: {cfg.get('mikrotik_type')}")

try:
    print("Mencoba mengambil data dari Mikrotik...")
    active, queues, secrets = get_mikrotik_data()
    print(f"Berhasil! Active: {len(active)}, Queues: {len(queues)}, Secrets: {len(secrets)}")
    if len(secrets) > 0:
        sample_key = list(secrets.keys())[0]
        print(f"Sample Secret -> Comment: '{sample_key}', Username: '{secrets[sample_key]}'")
    elif len(secrets) == 0:
        print("PERINGATAN: Secrets yang diterima adalah KOSONG (0)! Cek log error di atas (jika ada).")
except Exception as e:
    import traceback
    print("ERROR KERAS:")
    traceback.print_exc()
