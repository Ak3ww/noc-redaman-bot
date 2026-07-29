import sys
import os

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
sys.path.append(BASE_DIR)
from mikrotik_client import load_config
import routeros_api

cfg = load_config()
print("Config:", cfg.get("mikrotik_host"), cfg.get("mikrotik_username"), cfg.get("mikrotik_type"))

if cfg.get("mikrotik_type") == "api":
    try:
        connection = routeros_api.RouterOsApiPool(
            cfg.get("mikrotik_host"), 
            username=cfg.get("mikrotik_username"), 
            password=cfg.get("mikrotik_password"), 
            port=cfg.get("mikrotik_port", 8728), 
            plaintext_login=True
        )
        api = connection.get_api()
        secrets = api.get_resource('/ppp/secret').get()
        print("Fetched", len(secrets), "secrets from RouterOS API")
        if len(secrets) > 0: 
            print("Sample:", secrets[0])
    except Exception as e:
        print("Error connecting to Mikrotik API:", e)
elif cfg.get("mikrotik_type") == "rest":
    import requests
    proto = "https" if cfg.get("mikrotik_use_ssl") else "http"
    url = f"{proto}://{cfg.get('mikrotik_host')}:{cfg.get('mikrotik_port')}/rest"
    try:
        r = requests.get(f"{url}/ppp/secret", auth=(cfg.get("mikrotik_username"), cfg.get("mikrotik_password")), verify=False)
        secrets = r.json()
        print("Fetched", len(secrets), "secrets from REST API")
        if len(secrets) > 0: 
            print("Sample:", secrets[0])
    except Exception as e:
        print("Error connecting to Mikrotik REST:", e)
else:
    print("Mikrotik is disabled or unknown type")
