import duckdb

DB = "data/i2p.duckdb"

def run_router_geo_builder():
    con = duckdb.connect(DB)

    # Create tables if missing
    con.execute("""
        CREATE TABLE IF NOT EXISTS router_locations (
            router_hash TEXT PRIMARY KEY,
            latitude DOUBLE,
            longitude DOUBLE,
            timestamp TIMESTAMP
        )
    """)

    con.execute("""
        CREATE TABLE IF NOT EXISTS router_location_history (
            router_hash TEXT,
            latitude DOUBLE,
            longitude DOUBLE,
            timestamp TIMESTAMP,
            PRIMARY KEY (router_hash, timestamp)
        )
    """)

    # Pull latest geo data from enriched_router_data
    df = con.execute("""
        SELECT router_hash, latitude, longitude, timestamp
        FROM enriched_router_data
        WHERE latitude IS NOT NULL
          AND longitude IS NOT NULL
    """).df()

    for _, row in df.iterrows():
        # Insert directly — timestamp is already a TIMESTAMP
        con.execute("""
            INSERT OR REPLACE INTO router_locations
            VALUES (?, ?, ?, ?)
        """, [
            row.router_hash,
            row.latitude,
            row.longitude,
            row.timestamp
        ])

        # OR IGNORE: this function runs every pipeline cycle, but a router
        # only gets a new (router_hash, timestamp) pair here when its
        # enriched_router_data.timestamp actually advances (i.e. netDb
        # produced a fresh observation for it this cycle) -- routers that
        # weren't re-published since the last run would otherwise collide
        # with the row already recorded for that same timestamp and crash
        # the whole pipeline on every subsequent run.
        con.execute("""
            INSERT OR IGNORE INTO router_location_history
            VALUES (?, ?, ?, ?)
        """, [
            row.router_hash,
            row.latitude,
            row.longitude,
            row.timestamp
        ])

    con.close()

