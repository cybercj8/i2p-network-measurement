from collections import defaultdict
from .geoip_mapper import ip_to_country

def aggregate(feeds):
    counts = defaultdict(lambda: {"botnet":0, "malware":0, "spam":0, "exposed":0})

    # Spamhaus
    for ip in feeds["spamhaus"]["drop"]:
        c = ip_to_country(ip)
        if c: counts[c]["spam"] += 1

    for ip in feeds["spamhaus"]["edrop"]:
        c = ip_to_country(ip)
        if c: counts[c]["spam"] += 1

    for ip in feeds["spamhaus"]["c2"]:
        c = ip_to_country(ip)
        if c: counts[c]["botnet"] += 1

    # Abuse.ch
    for ip in feeds["abusech"]["threatfox"]:
        c = ip_to_country(ip)
        if c: counts[c]["malware"] += 1

    for ip in feeds["abusech"]["malwarebazaar"]:
        c = ip_to_country(ip)
        if c: counts[c]["malware"] += 1

    # URLHaus hosts → treat as exposed services
    for host in feeds["abusech"]["urlhaus"]:
        # host may not be IP; skip for now
        pass

    return counts

