import duckdb
import time
from datetime import datetime
import pandas as pd

DB_PATH = "data/i2p.duckdb"


def connect_db():
    return duckdb.connect(DB_PATH)


def ensure_timeseries_tables(conn):
    # Main timeseries table.
    # This previously had no PRIMARY KEY and was written via plain INSERT,
    # not INSERT OR IGNORE -- so any router whose latest snapshot didn't
    # change between pipeline runs (e.g. not freshly re-observed that
    # cycle) got a brand new duplicate (router_hash, timestamp) row every
    # single run. Found via a visible symptom: the dashboard's "Global
    # Router Evolution" chart showing an impossible spike to 19,008 routers
    # in one minute, when only 2,960 distinct router_hash values actually
    # existed for that bucket -- the other ~16,000 rows were exact
    # duplicates. Migrate any pre-existing table by deduplicating before
    # adding the constraint (a fresh CREATE TABLE IF NOT EXISTS is a no-op
    # against an already-existing table and won't retroactively add a PK).
    table_existed = conn.execute("""
        SELECT COUNT(*) FROM information_schema.tables WHERE table_name = 'timeseries_router_stats'
    """).fetchone()[0] > 0

    if table_existed:
        conn.execute("""
            CREATE TABLE timeseries_router_stats_dedup AS
            SELECT * FROM timeseries_router_stats
            QUALIFY ROW_NUMBER() OVER (PARTITION BY router_hash, timestamp ORDER BY router_hash) = 1
        """)
        conn.execute("DROP TABLE timeseries_router_stats")
        conn.execute("ALTER TABLE timeseries_router_stats_dedup RENAME TO timeseries_router_stats")
        conn.execute("""
            ALTER TABLE timeseries_router_stats
            ADD PRIMARY KEY (router_hash, timestamp)
        """)
    else:
        conn.execute("""
        CREATE TABLE timeseries_router_stats (
            router_hash TEXT,
            timestamp TIMESTAMP,
            ip TEXT,
            ipv6 BOOLEAN,
            asn INTEGER,
            caps TEXT,
            version TEXT,
            transport TEXT,
            country TEXT,
            region TEXT,
            city TEXT,
            uptime_hours DOUBLE,
            churn_event TEXT,
            PRIMARY KEY (router_hash, timestamp)
        )
        """)

    # Last-seen table
    conn.execute("""
    CREATE TABLE IF NOT EXISTS router_last_timeseries AS
    SELECT * FROM timeseries_router_stats WHERE FALSE
    """)

    # get_recent_churn_event() below reads FROM router_churn, but that table
    # is normally created by ChurnBuilder -- which runs AFTER this step in
    # the pipeline, and even then only if it finds actual churn events (none
    # exist yet on a router's first-ever observation). Ensure it exists
    # (schema matches asn_intel/churn_builder.py's ensure_churn_table())
    # so the query below never fails on an empty/fresh database.
    conn.execute("""
    CREATE TABLE IF NOT EXISTS router_churn (
        router_hash TEXT,
        timestamp TIMESTAMP,
        event TEXT,
        old_ip TEXT,
        new_ip TEXT,
        old_asn INTEGER,
        new_asn INTEGER,
        old_caps TEXT,
        new_caps TEXT,
        old_transport TEXT,
        new_transport TEXT
    )
    """)

    # Ensure router_behavior has uptime_slope -- only relevant when upgrading
    # a pre-existing database from an older schema; on a fresh database this
    # table doesn't exist yet (rebuild_behavior.py creates it from scratch
    # later in the pipeline), so skip rather than fail.
    table_exists = conn.execute("""
        SELECT COUNT(*) FROM information_schema.tables WHERE table_name = 'router_behavior'
    """).fetchone()[0] > 0
    if table_exists:
        conn.execute("""
        ALTER TABLE router_behavior
        ADD COLUMN IF NOT EXISTS uptime_slope DOUBLE
        """)

    conn.commit()


def get_latest_snapshots(conn):
    return conn.execute("""
        SELECT router_hash, timestamp, ip, ipv6, transport, caps, version
        FROM router_snapshots
        QUALIFY ROW_NUMBER() OVER (PARTITION BY router_hash ORDER BY timestamp DESC) = 1
    """).fetchall()


def get_last_timeseries(conn):
    rows = conn.execute("""
        SELECT *
        FROM router_last_timeseries
        QUALIFY ROW_NUMBER() OVER (PARTITION BY router_hash ORDER BY timestamp DESC) = 1
    """).fetchall()

    last = {}
    for row in rows:
        (
            router_hash, ts, ip, ipv6, asn, caps, version, transport,
            country, region, city, uptime_hours, churn_event
        ) = row
        last[router_hash] = {
            "timestamp": ts,
            "uptime_hours": uptime_hours,
        }
    return last


def estimate_uptime(previous, current_ts):
    if previous is None:
        return 0.0

    prev_ts = pd.to_datetime(previous["timestamp"], utc=True)
    current_ts = pd.to_datetime(current_ts, utc=True)

    delta_hours = (current_ts - prev_ts).total_seconds() / 3600
    return max(delta_hours, 0.0)


def get_recent_churn_event(conn, router_hash):
    row = conn.execute("""
        SELECT event
        FROM router_churn
        WHERE router_hash = ?
        ORDER BY timestamp DESC
        LIMIT 1
    """, (router_hash,)).fetchone()

    return row[0] if row else None


def write_timeseries_row(conn, row):
    conn.execute("""
        INSERT OR IGNORE INTO timeseries_router_stats
        (router_hash, timestamp, ip, ipv6, asn, caps, version, transport,
         country, region, city, uptime_hours, churn_event)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
    """, row)
    conn.commit()


def update_last_timeseries(conn):
    conn.execute("DELETE FROM router_last_timeseries")
    conn.execute("""
        INSERT INTO router_last_timeseries
        SELECT *
        FROM timeseries_router_stats
        QUALIFY ROW_NUMBER() OVER (PARTITION BY router_hash ORDER BY timestamp DESC) = 1
    """)
    conn.commit()


def run_router_timeseries():
    conn = connect_db()
    ensure_timeseries_tables(conn)

    print("[*] Loading latest snapshots...")
    snapshots = get_latest_snapshots(conn)

    print("[*] Loading previous timeseries state...")
    previous = get_last_timeseries(conn)

    print("[*] Building router timeseries...")

    for row in snapshots:
        (
            router_hash, ts, ip, ipv6, transport, caps, version
        ) = row

        if isinstance(ts, str):
            ts = pd.to_datetime(ts, utc=True)

        # Placeholder fields
        asn = None
        country = None
        region = None
        city = None

        prev = previous.get(router_hash)
        uptime_hours = estimate_uptime(prev, ts)
        churn_event = get_recent_churn_event(conn, router_hash)

        # Write timeseries row
        timeseries_row = (
            router_hash,
            ts,
            ip,
            ipv6,
            asn,
            caps,
            version,
            transport,
            country,
            region,
            city,
            uptime_hours,
            churn_event,
        )

        write_timeseries_row(conn, timeseries_row)


        print(f"[TS] {router_hash} uptime={uptime_hours:.2f}h churn={churn_event}")

    update_last_timeseries(conn)
    print("[*] Router timeseries cycle complete.")

