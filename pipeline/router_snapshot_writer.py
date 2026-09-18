import os
import time
import duckdb
from pathlib import Path
from datetime import datetime, timezone

from ingest.java_routerinfo import parse_routerinfo_java

# Two independent vantage points: the Java I2P router and i2pd (C++
# implementation). Both build their own local netDb by gossiping with the
# real I2P network, so comparing which routers each one observed answers a
# real research question -- do different implementations see the same
# slice of the network, or meaningfully different ones.
NETDB_PATHS = {
    "java_i2p": Path.home() / "Library/Application Support/i2p/netDb",
    "i2pd": Path("/opt/homebrew/var/lib/i2pd/netDb"),
}
DB_PATH = "data/i2p.duckdb"


def connect_db():
    return duckdb.connect(DB_PATH)


def ensure_tables(conn):
    conn.execute("""
    CREATE TABLE IF NOT EXISTS router_snapshots (
        router_hash TEXT,
        timestamp TIMESTAMP WITH TIME ZONE,
        ip TEXT,
        ipv6 BOOLEAN,
        transport TEXT,
        caps TEXT,
        version TEXT,
        seen_by TEXT,
        published BIGINT,
        PRIMARY KEY (router_hash, timestamp, seen_by)
    )
    """)
    # Defensive: CREATE TABLE IF NOT EXISTS is a no-op against a table that
    # already existed before this column was added (same lesson learned
    # earlier this project -- see router_timeseries_builder.py).
    conn.execute("ALTER TABLE router_snapshots ADD COLUMN IF NOT EXISTS seen_by TEXT")
    # published = router's own self-reported RouterInfo publish time (epoch
    # milliseconds, from the RouterInfo itself) -- a genuine router-side
    # signal, unlike router_age_days/uptime_hours which only measure how
    # long *we've* been observing it.
    conn.execute("ALTER TABLE router_snapshots ADD COLUMN IF NOT EXISTS published BIGINT")
    conn.commit()


def find_routerinfo_files():
    for vantage_point, netdb_path in NETDB_PATHS.items():
        if not netdb_path.exists():
            print(f"[*] Vantage point '{vantage_point}' netDb not found at {netdb_path}, skipping")
            continue
        for root, dirs, files in os.walk(netdb_path):
            for f in files:
                if f.startswith("routerInfo-") and f.endswith(".dat"):
                    yield os.path.join(root, f), vantage_point


def parse_raw_routerinfo(raw_bytes):
    try:
        text = raw_bytes.decode("utf-8", errors="ignore")

        ident = None
        if "routerIdentity" in text:
            start = text.find("routerIdentity") + len("routerIdentity")
            end = text.find("}", start)
            ident = text[start:end].strip()

        caps = None
        if "caps" in text:
            start = text.find("caps") + len("caps")
            end = text.find("\n", start)
            caps = text[start:end].strip()

        ip = None
        transport = None
        version = None

        for line in text.splitlines():
            if "host=" in line:
                ip = line.split("host=")[-1].strip()
            if "transport=" in line:
                transport = line.split("transport=")[-1].strip()
            if "routerVersion" in line:
                version = line.split("=")[-1].strip()

        ipv6 = ip is not None and ":" in ip

        return {
            "router_hash": ident,
            "ip": ip,
            "ipv6": ipv6,
            "transport": transport,
            "caps": caps,
            "version": version,
            # published is binary-encoded in the raw RouterInfo bytes, not
            # reliably extractable via this crude text-search fallback --
            # left unset rather than guessed.
            "published": None,
        }

    except Exception:
        return None


def parse_routerinfo(path):
    try:
        with open(path, "rb") as f:
            raw = f.read()

        parsed = parse_routerinfo_java(raw)
        if parsed:
            return {
                "router_hash": parsed.get("router_hash"),
                "ip": parsed.get("ip"),
                # parse_routerinfo_java() returns this field under the key
                # "supports_ipv6", not "ipv6" -- the mismatched key here
                # meant every call silently returned None, which downstream
                # coerced to False everywhere (supports_ipv6 was 0% across
                # the entire dataset despite ~15% of routers genuinely
                # having an IPv6 address).
                "ipv6": parsed.get("supports_ipv6"),
                "transport": parsed.get("transport"),
                "caps": parsed.get("caps"),
                "version": parsed.get("version"),
                "published": parsed.get("published"),
            }

        return parse_raw_routerinfo(raw)

    except Exception:
        return None


def write_snapshot(conn, meta):
    conn.execute("""
        INSERT OR IGNORE INTO router_snapshots
        (router_hash, timestamp, ip, ipv6, transport, caps, version, seen_by, published)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
    """, (
        meta["router_hash"],
        datetime.fromtimestamp(time.time(), tz=timezone.utc),  # FIXED: proper TIMESTAMP
        meta["ip"],
        meta["ipv6"],
        meta["transport"],
        meta["caps"],
        meta["version"],
        meta.get("seen_by"),
        meta.get("published"),
    ))
    conn.commit()


def run_snapshot_cycle():
    conn = connect_db()
    ensure_tables(conn)

    print("[*] Starting router snapshot cycle...")

    parsed_count = 0
    skipped = 0
    counts_by_vantage = {}

    for path, vantage_point in find_routerinfo_files():
        meta = parse_routerinfo(path)

        if not meta or not meta.get("router_hash"):
            skipped += 1
            continue

        meta["seen_by"] = vantage_point
        write_snapshot(conn, meta)
        parsed_count += 1
        counts_by_vantage[vantage_point] = counts_by_vantage.get(vantage_point, 0) + 1

    print(f"[*] Snapshot cycle complete. Parsed={parsed_count}, Skipped={skipped}")
    for vp, count in counts_by_vantage.items():
        print(f"    seen_by={vp}: {count}")


if __name__ == "__main__":
    run_snapshot_cycle()

