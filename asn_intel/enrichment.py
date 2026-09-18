import duckdb
import geoip2.database
import ipaddress
import pandas as pd
from pathlib import Path

DB_PATH = "data/i2p.duckdb"

ASN_DB = Path("GeoLite2-ASN.mmdb")
CITY_DB = Path("GeoLite2-City.mmdb")


def extract_ipv4(ip_field):
    if not ip_field:
        return None
    try:
        ip = ipaddress.ip_address(ip_field)
        if isinstance(ip, ipaddress.IPv4Address):
            return ip_field
    except Exception:
        pass
    return None


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


def build_asn_info():
    con = duckdb.connect(DB_PATH)

    # DROP+CREATE rather than IF NOT EXISTS: this table is fully rebuilt on
    # every run anyway (DELETE + fresh INSERT below), and IF NOT EXISTS is a
    # no-op against a table that already exists with an older schema -- bit
    # us more than once this project when a column got renamed but a stale
    # already-existing table silently kept the old column names.
    con.execute("DROP TABLE IF EXISTS asn_info")
    con.execute("""
        CREATE TABLE asn_info (
            asn INTEGER,
            as_org TEXT,
            country TEXT,
            router_count INTEGER,
            floodfill_count INTEGER,
            ipv6_count INTEGER,
            ntcp2_count INTEGER,
            ssu2_count INTEGER,
            ssu_count INTEGER,
            avg_age DOUBLE,
            risk_score DOUBLE,
            avg_lat DOUBLE,
            avg_lon DOUBLE,
            cluster_id INTEGER,
            reputation_tier VARCHAR,   -- FIXED: now TEXT
            anomaly_score DOUBLE,
            caps_raw TEXT,
            version_raw TEXT,
            version_major INTEGER,
            version_minor INTEGER,
            version_patch INTEGER,
            version_build INTEGER,
            total_floodfill INTEGER,
            total_reachable INTEGER,
            total_hidden INTEGER,
            total_unreachable INTEGER,
            total_congested_medium INTEGER,
            total_congested_high INTEGER,
            total_rejecting_tunnels INTEGER,
            total_bw_low INTEGER,
            total_bw_mid INTEGER,
            total_bw_high INTEGER,
            total_bw_unlimited INTEGER,
            total_ssu INTEGER,
            total_ssu2 INTEGER,
            total_ntcp INTEGER,
            total_ntcp2 INTEGER,
            total_ipv6 INTEGER
        )
    """)

    # Source directly from enriched_router_data instead of redoing raw
    # IP/GeoIP/ASN lookups here -- this function used to have its own copy of
    # extract_ipv4() (IPv4-only, same bug fixed in router_enrichment.py this
    # session) which silently dropped ASN/geo stats for every IPv6-only
    # router. enriched_router_data already has correct per-router asn/geo/
    # capability columns computed once, so aggregate from there.
    con.execute("""
        INSERT INTO asn_info
        SELECT
            CAST(asn AS INTEGER),
            ANY_VALUE(as_org),
            ANY_VALUE(country_geo),
            COUNT(*) AS router_count,
            SUM(is_floodfill_cap::INTEGER),
            SUM(supports_ipv6::INTEGER),
            SUM(supports_ntcp2::INTEGER),
            SUM(supports_ssu2::INTEGER),
            SUM(supports_ssu::INTEGER),
            AVG(EXTRACT(epoch FROM router_age_days) / 86400.0) AS avg_router_age,
            NULL AS risk_score,
            AVG(latitude),
            AVG(longitude),
            NULL AS cluster_id,
            NULL AS reputation_tier,
            0.0 AS anomaly_score,
            ANY_VALUE(caps_raw),
            ANY_VALUE(version_raw),
            ANY_VALUE(version_major),
            ANY_VALUE(version_minor),
            ANY_VALUE(version_patch),
            ANY_VALUE(version_build),
            SUM(is_floodfill_cap::INTEGER),
            SUM(is_reachable::INTEGER),
            SUM(is_hidden::INTEGER),
            SUM(is_unreachable::INTEGER),
            SUM(is_congested_medium::INTEGER),
            SUM(is_congested_high::INTEGER),
            SUM(is_rejecting_tunnels::INTEGER),
            SUM(bw_low::INTEGER),
            SUM(bw_mid::INTEGER),
            SUM(bw_high::INTEGER),
            SUM(bw_unlimited::INTEGER),
            SUM(supports_ssu::INTEGER),
            SUM(supports_ssu2::INTEGER),
            SUM(supports_ntcp::INTEGER),
            SUM(supports_ntcp2::INTEGER),
            SUM(supports_ipv6::INTEGER)
        FROM enriched_router_data
        WHERE asn IS NOT NULL
        GROUP BY asn
    """)

    con.close()


def run_asn_enrichment():
    build_asn_info()


if __name__ == "__main__":
    run_asn_enrichment()

