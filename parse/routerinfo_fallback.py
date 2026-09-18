import time

def fallback_parse(path):
    try:
        data = open(path, "rb").read()

        return {
            "version": None,
            "caps": None,
            "is_floodfill": 0,
            "bandwidth": None,
            "ntcp2": None,
            "ssu2": None,
            "ipv6": None,
            "published": None,
            "last_seen": int(time.time())
        }

    except Exception:
        return None

