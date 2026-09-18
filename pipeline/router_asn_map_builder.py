import duckdb
import geoip2.database
import ipaddress
from pathlib import Path

DB_PATH = "data/i2p.duckdb"
ASN_DB = Path("GeoLite2-ASN.mmdb")


def connect_db():
    return duckdb.connect(DB_PATH)


def ensure_asn_map_table(conn):
    conn.execute("""
    CREATE TABLE IF NOT EXISTS router_asn_map (
        router_hash TEXT,
        timestamp TIMESTAMP,
        asn INTEGER,
        as_org TEXT,
        country TEXT,
        region TEXT,
        city TEXT,
        PRIMARY KEY (router_hash, timestamp)
    )
    """)
    conn.execute("""
    CREATE TABLE IF NOT EXISTS router_last_asn_map AS
    SELECT * FROM router_asn_map WHERE FALSE
    """)
    conn.commit()


def extract_ip_for_asn(ip_field):
    """Return ip_field if it's any parseable IP (v4 or v6) -- GeoLite2-ASN.mmdb
    resolves both natively. This was IPv4-only before (a third, independent
    copy of the same bug fixed earlier this session in router_enrichment.py
    and asn_intel/enrichment.py), which silently skipped ASN lookup entirely
    for every IPv6-only router."""
    if not ip_field:
        return None
    try:
        ipaddress.ip_address(ip_field)
        return ip_field
    except Exception:
        return None


def get_latest_snapshots(conn):
    return conn.execute("""
        SELECT
            s.router_hash,
            s.timestamp,
            s.ip
        FROM router_snapshots s
        QUALIFY ROW_NUMBER() OVER (
            PARTITION BY router_hash ORDER BY timestamp DESC
        ) = 1
    """).fetchall()


def get_last_asn_map(conn):
    rows = conn.execute("""
        SELECT *
        FROM router_last_asn_map
        QUALIFY ROW_NUMBER() OVER (
            PARTITION BY router_hash ORDER BY timestamp DESC
        ) = 1
    """).fetchall()

    last = {}
    for row in rows:
        router_hash, ts, asn, as_org, country, region, city = row
        last[router_hash] = {
            "asn": asn,
            "as_org": as_org,
            "country": country
        }
    return last


def write_asn_map_row(conn, row):
    conn.execute("""
        INSERT OR IGNORE INTO router_asn_map
        (router_hash, timestamp, asn, as_org, country, region, city)
        VALUES (?, ?, ?, ?, ?, ?, ?)
    """, row)
    conn.commit()


def update_last_asn_map(conn):
    conn.execute("DELETE FROM router_last_asn_map")
    conn.execute("""
        INSERT INTO router_last_asn_map
        SELECT *
        FROM router_asn_map
        QUALIFY ROW_NUMBER() OVER (
            PARTITION BY router_hash ORDER BY timestamp DESC
        ) = 1
    """)
    conn.commit()


def run_router_asn_map():
    conn = connect_db()
    ensure_asn_map_table(conn)

    print("[*] Loading latest router snapshots...")
    snapshots = get_latest_snapshots(conn)

    print("[*] Loading previous ASN map state...")
    previous = get_last_asn_map(conn)

    print("[*] Opening GeoLite2 ASN database...")
    asn_reader = geoip2.database.Reader(str(ASN_DB))

    print("[*] Building ASN map...")

    for router_hash, ts, ip in snapshots:
        ip_for_lookup = extract_ip_for_asn(ip)
        if not ip_for_lookup:
            continue

        try:
            resp = asn_reader.asn(ip_for_lookup)
            asn = resp.autonomous_system_number
            as_org = resp.autonomous_system_organization
        except Exception:
            asn = None
            as_org = None

        prev = previous.get(router_hash)

        if (
            prev is None
            or prev["asn"] != asn
            or prev["as_org"] != as_org
        ):
            row = (
                router_hash,
                ts,
                asn,
                as_org,
                None,
                None,
                None
            )
            write_asn_map_row(conn, row)
            print(f"[ASN MAP] {router_hash} ASN={asn} ORG={as_org}")

    asn_reader.close()
    update_last_asn_map(conn)
    print("[*] Router ASN map cycle complete.")

