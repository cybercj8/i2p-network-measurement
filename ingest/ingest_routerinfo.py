import os
import duckdb
from datetime import datetime

from ingest.java_routerinfo import parse_routerinfo_java
from ingest.asn_lookup import lookup_asn
from ingest.geo_lookup import lookup_geo

DB_PATH = "asn_intel.duckdb"

VALID_MAGIC = {0x1F, 0x78, 0xDD, 0x00}

def iter_routerinfo_files(netdb_path):
    """
    Yield only valid routerInfo files from r0–rZ buckets.
    """
    for entry in os.listdir(netdb_path):
        # Only r0–rZ directories
        if len(entry) == 2 and entry.startswith("r") and entry[1].isalnum():
            if entry in ("r~", "r-"):
                continue

            bucket = os.path.join(netdb_path, entry)
            if not os.path.isdir(bucket):
                continue

            for fname in os.listdir(bucket):
                if fname.startswith("routerInfo-") and fname.endswith(".dat"):
                    yield os.path.join(bucket, fname)

def ingest_netdb(netdb_path):
    conn = duckdb.connect(DB_PATH)
    timestamp = datetime.utcnow()

    total_files = 0
    parsed_files = 0

    for full_path in iter_routerinfo_files(netdb_path):
        total_files += 1

        try:
            with open(full_path, "rb") as f:
                raw = f.read()
        except Exception as e:
            print(f"[WARN] Could not read file {full_path}: {e}")
            continue

        # Skip files with wrong magic byte
        if raw[0] not in VALID_MAGIC:
            print(f"[SKIP] Invalid magic byte in {full_path}")
            continue

        parsed = parse_routerinfo_java(raw)
        if not parsed or not parsed.get("router_hash"):
            print(f"[SKIP] Unparsed routerinfo: {full_path}")
            continue

        parsed_files += 1
        router_hash = parsed["router_hash"]

        ip = parsed.get("ip")
        if not ip:
            print(f"[SKIP] No IP found for {router_hash}")
            continue

        asn_info = lookup_asn(ip)
        geo_info = lookup_geo(ip)

        conn.execute("""
            INSERT INTO router_snapshots
            (router_hash, timestamp, version, caps, is_floodfill, supports_ipv6, transport, published)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?)
        """, [
            router_hash,
            timestamp,
            parsed.get("version"),
            parsed.get("caps"),
            parsed.get("is_floodfill"),
            parsed.get("supports_ipv6"),
            parsed.get("transport"),
            parsed.get("published")
        ])

        conn.execute("""
            INSERT INTO router_metadata
            (router_hash, first_seen, last_seen, total_seen, country, latitude, longitude, asn, asn_name)
            VALUES (?, ?, ?, 1, ?, ?, ?, ?, ?)
            ON CONFLICT(router_hash) DO UPDATE SET
                last_seen = excluded.last_seen,
                total_seen = router_metadata.total_seen + 1,
                country = excluded.country,
                latitude = excluded.latitude,
                longitude = excluded.longitude,
                asn = excluded.asn,
                asn_name = excluded.asn_name
        """, [
            router_hash,
            timestamp,
            timestamp,
            geo_info["country"],
            geo_info["latitude"],
            geo_info["longitude"],
            asn_info["asn"],
            asn_info["asn_name"]
        ])

        conn.execute("""
            INSERT INTO router_asn_map
            (router_hash, timestamp, asn, asn_name, country, latitude, longitude)
            VALUES (?, ?, ?, ?, ?, ?, ?)
        """, [
            router_hash,
            timestamp,
            asn_info["asn"],
            asn_info["asn_name"],
            geo_info["country"],
            geo_info["latitude"],
            geo_info["longitude"]
        ])

    conn.close()

    print(f"[DONE] Scanned: {total_files} routerInfo files")
    print(f"[DONE] Parsed: {parsed_files} routerInfo files")

