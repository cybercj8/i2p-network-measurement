import base64
import json
import re
from datetime import datetime

def parse_routerinfo(raw_bytes):
    """
    Parse a raw routerinfo file into structured fields.
    """
    try:
        text = raw_bytes.decode("utf-8", errors="ignore")

        # Extract router hash
        match_hash = re.search(r"routerHash=([A-Za-z0-9+/=]+)", text)
        router_hash = match_hash.group(1) if match_hash else None

        # Extract published timestamp
        match_pub = re.search(r"published=(\d+)", text)
        published = datetime.utcfromtimestamp(int(match_pub.group(1)) / 1000) if match_pub else None

        # Extract version
        match_ver = re.search(r"routerVersion=([0-9.]+)", text)
        version = match_ver.group(1) if match_ver else None

        # Extract capabilities
        match_caps = re.search(r"caps=([A-Za-z]+)", text)
        caps = match_caps.group(1) if match_caps else ""

        is_floodfill = "f" in caps
        supports_ipv6 = "6" in caps

        # Extract transport type
        match_transport = re.search(r"transport=([A-Za-z0-9]+)", text)
        transport = match_transport.group(1) if match_transport else None

        return {
            "router_hash": router_hash,
            "published": published,
            "version": version,
            "caps": caps,
            "is_floodfill": is_floodfill,
            "supports_ipv6": supports_ipv6,
            "transport": transport,
        }

    except Exception as e:
        print("Parse error:", e)
        return None

