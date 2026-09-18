import requests

THREATFOX_URL = "https://threatfox-api.abuse.ch/api/v1/"
URLHAUS_URL = "https://urlhaus-api.abuse.ch/v1/urls/recent/"
MALWAREBAZAAR_URL = "https://mb-api.abuse.ch/api/v1/"

def load_threatfox():
    data = {"query": "get_iocs", "days": 1}
    resp = requests.post(THREATFOX_URL, json=data, timeout=10)
    return [x.get("ip_address") for x in resp.json().get("data", []) if x.get("ip_address")]

def load_urlhaus():
    resp = requests.get(URLHAUS_URL, timeout=10)
    return [x.get("host") for x in resp.json().get("urls", [])]

def load_malwarebazaar():
    data = {"query": "get_recent"}
    resp = requests.post(MALWAREBAZAAR_URL, data=data, timeout=10)
    return [x.get("origin_ip") for x in resp.json().get("data", []) if x.get("origin_ip")]

def load_abusech():
    return {
        "threatfox": load_threatfox(),
        "urlhaus": load_urlhaus(),
        "malwarebazaar": load_malwarebazaar()
    }

