import duckdb
import ipaddress
import time
import geoip2.database
from pathlib import Path
import pandas as pd

DB_PATH = "data/i2p.duckdb"

ASN_DB = Path("GeoLite2-ASN.mmdb")
CITY_DB = Path("GeoLite2-City.mmdb")


def extract_ip_for_geo(ip_field):
    """Return ip_field if it's any parseable IP (v4 or v6) -- GeoLite2-City.mmdb
    resolves both natively, so there's no reason to restrict to IPv4 only
    (doing so silently dropped geolocation for every IPv6-only router)."""
    if not ip_field:
        return None
    try:
        ipaddress.ip_address(ip_field)
        return ip_field
    except Exception:
        return None


def parse_caps(caps_raw):
    # Letter meanings per the authoritative I2P NetDB spec
    # (https://i2p.net/en/docs/overview/network-database#routerinfo):
    #   f=floodfill H=hidden R=reachable U=unreachable
    #   D=medium congestion E=high congestion G=rejecting all tunnels (0.9.58+)
    #   Bandwidth tier (shared bw): K<12 L12-48 M48-64 N64-128 O128-256 P256-2000 X>2000 KBps
    # The previous version of this function checked "E" for "is_introducer"
    # and "P" for "supports_peer_test" -- neither is correct per spec (E is a
    # congestion flag, P is a bandwidth tier), and "C"/"V" ("supports_client_
    # only"/"supports_version_flag") aren't real capability letters at all,
    # which is why they always evaluated to 0 across the entire dataset. The
    # bandwidth bucketing below was also incomplete: it only ever checked
    # L/M/N/O, silently never counting the K (slowest) or P/X (two fastest)
    # tiers, and mislabeled O (128-256 KBps) as "unlimited".
    caps_raw = caps_raw or ""
    return {
        "is_floodfill_cap": "f" in caps_raw,
        "is_reachable": "R" in caps_raw,
        "is_hidden": "H" in caps_raw,
        "is_unreachable": "U" in caps_raw,
        "is_congested_medium": "D" in caps_raw,
        "is_congested_high": "E" in caps_raw,
        "is_rejecting_tunnels": "G" in caps_raw,
        "bw_low": ("K" in caps_raw) or ("L" in caps_raw),
        "bw_mid": ("M" in caps_raw) or ("N" in caps_raw),
        "bw_high": ("O" in caps_raw) or ("P" in caps_raw),
        "bw_unlimited": "X" in caps_raw,
    }


def parse_version(version_raw):
    if not version_raw:
        return dict(
            version_major=None,
            version_minor=None,
            version_patch=None,
            version_build=None
        )

    parts = version_raw.replace("-", ".").split(".")
    parts = [p for p in parts if p.isdigit()]

    major = int(parts[0]) if len(parts) > 0 else None
    minor = int(parts[1]) if len(parts) > 1 else None
    patch = int(parts[2]) if len(parts) > 2 else None
    build = int(parts[3]) if len(parts) > 3 else None

    return dict(
        version_major=major,
        version_minor=minor,
        version_patch=patch,
        version_build=build
    )


def geo_lookup(ip, city_reader):
    if not ip:
        return (None, None, None)
    try:
        resp = city_reader.city(ip)
        return (
            resp.location.latitude,
            resp.location.longitude,
            resp.country.iso_code
        )
    except Exception:
        return (None, None, None)


def compute_router_age(con, router_hash):
    rows = con.execute("""
        SELECT MIN(timestamp), MAX(timestamp)
        FROM router_snapshots
        WHERE router_hash = ?
    """, [router_hash]).fetchone()

    if not rows or not rows[0] or not rows[1]:
        return 0.0

    first_seen, last_seen = rows
    age_ms = last_seen - first_seen
    return age_ms / 86400000.0  # ms → days


def run_router_enrichment():
    con = duckdb.connect(DB_PATH)

    # seen_by/implementation here reflect only the *latest* snapshot per
    # router (same QUALIFY pattern as everything else in this table), so a
    # router seen by both vantage points shows just its most recent one --
    # for the actual overlap analysis (how much do the two vantage points'
    # views differ), query router_snapshots directly, which retains every
    # historical (router_hash, seen_by) pair, not just the latest.
    routers = con.execute("""
        SELECT
            s.router_hash,
            s.timestamp,
            s.ip,
            s.ipv6,
            s.transport,
            s.caps,
            s.version,
            m.asn,
            m.as_org AS as_org,
            m.country AS asn_country,
            s.seen_by,
            ki.implementation,
            s.published
        FROM router_snapshots s
        LEFT JOIN router_last_asn_map m USING (router_hash)
        LEFT JOIN known_implementations ki USING (router_hash)
        QUALIFY ROW_NUMBER() OVER (
            PARTITION BY s.router_hash ORDER BY s.timestamp DESC
        ) = 1
    """).fetchall()

    columns = [
        "router_hash", "timestamp", "ip", "ipv6",
        "transport", "caps_raw", "version_raw",
        "asn", "as_org", "asn_country",
        "seen_by", "implementation", "published"
    ]

    asn_reader = geoip2.database.Reader(str(ASN_DB))
    city_reader = geoip2.database.Reader(str(CITY_DB))

    enriched = []

    for row in routers:
        r = dict(zip(columns, row))

        geo_ip = extract_ip_for_geo(r["ip"])
        caps = parse_caps(r["caps_raw"])
        version = parse_version(r["version_raw"])

        lat, lon, country_geo = geo_lookup(geo_ip, city_reader)
        router_age = compute_router_age(con, r["router_hash"])

        # days_since_published is a router-self-reported signal (from its
        # own RouterInfo publish timestamp), independent of how long *we*
        # have been observing it -- a genuine complement to router_age_days/
        # uptime_hours, which only reflect our own observation window.
        published_ms = r["published"]
        days_since_published = (
            (time.time() * 1000 - published_ms) / 86400000.0
            if published_ms is not None else None
        )

        enriched.append({
            "router_hash": r["router_hash"],
            "timestamp": r["timestamp"],
            "ip": r["ip"],
            "ipv6": r["ipv6"],
            "asn": r["asn"],
            "as_org": r["as_org"],
            "asn_country": r["asn_country"],
            "country_geo": country_geo,
            "latitude": lat,
            "longitude": lon,
            "router_age_days": router_age,
            "days_since_published": days_since_published,
            "transport": r["transport"],
            "caps_raw": r["caps_raw"],
            "version_raw": r["version_raw"],
            "seen_by": r["seen_by"],
            "implementation": r["implementation"],
            **caps,
            **version,
            "supports_ipv6": bool(r["ipv6"]),
            "supports_ntcp2": "NTCP2" in (r["transport"] or ""),
            "supports_ntcp": "NTCP" in (r["transport"] or ""),
            "supports_ssu2": "SSU2" in (r["transport"] or ""),
            "supports_ssu": "SSU" in (r["transport"] or "")
        })

    df = pd.DataFrame(enriched).reset_index(drop=True)

    con.register("router_enriched_df", df)

    # ⭐ Drop old schema so types match
    con.execute("DROP TABLE IF EXISTS enriched_router_data")

    # ⭐ Recreate with correct schema
    con.execute("""
        CREATE TABLE enriched_router_data AS
        SELECT * FROM router_enriched_df LIMIT 0
    """)

    con.execute("""
        INSERT INTO enriched_router_data
        SELECT * FROM router_enriched_df
    """)

    asn_reader.close()
    city_reader.close()
    con.close()


if __name__ == "__main__":
    run_router_enrichment()

