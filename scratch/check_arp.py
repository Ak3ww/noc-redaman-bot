import routeros_api
api = routeros_api.RouterOsApiPool('103.157.79.178', username='billinghub.id', password='@eugine0909@', port=8520, plaintext_login=True).get_api()
arp = api.get_resource('/ip/arp').get()
for a in arp:
    if a.get('address', '').startswith('192.168.30.'):
        print(f"IP: {a.get('address')}, MAC: {a.get('mac-address')}, Interface: {a.get('interface')}")
