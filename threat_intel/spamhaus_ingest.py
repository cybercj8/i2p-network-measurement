import requests
import re

DROP_URL = "https://www.spamhaus.org/drop/drop.txt"
EDROP_URL = "https://www.spamhaus.org/drop/edrop.txt"
BOTNET_C2_URL = "https://www.spamhaus.org/drop/drop.txt"  # placeholder C2 feed

IP_REGEX = re.compile(r"(\d+\.\d+\.\d+\.\d+)")

def fetch_ips(url):
    resp = requests.get(url, timeout=10)
    ips = IP_REGEX.findall(resp.text)
    return ips

def load_spamhaus():
    drop_ips = fetch_ips(DROP_URL)
    edrop_ips = fetch_ips(EDROP_URL)
    c2_ips = fetch_ips(BOTNET_C2_URL)
    return {
        "drop": drop_ips,
        "edrop": edrop_ips,
        "c2": c2_ips
    }

