import routeros_api
api = routeros_api.RouterOsApiPool('103.157.79.178', username='billinghub.id', password='@eugine0909@', port=8520, plaintext_login=True).get_api()
vlans = api.get_resource('/interface/vlan').get()
for v in vlans:
    if 'vlan30' in v['name']:
        print(f"VLAN: {v['name']}, Interface: {v.get('interface')}")
