import duckdb
import numpy as np
from sklearn.cluster import KMeans
from sklearn.preprocessing import StandardScaler
import time

DB_PATH = "data/i2p.duckdb"


def connect_db():
    return duckdb.connect(DB_PATH)


def ensure_cluster_table(conn):
    conn.execute("""
    CREATE TABLE IF NOT EXISTS router_clusters (
        router_hash TEXT,
        timestamp BIGINT,
        cluster INTEGER,
        PRIMARY KEY (router_hash, timestamp)
    )
    """)
    conn.commit()


def load_feature_matrix(conn):
    """
    Loads the latest router timeseries + ASN map into a clean feature matrix.
    All missing values are sanitized.
    """
    rows = conn.execute("""
        SELECT
            t.router_hash,
            t.uptime_hours,
            t.asn,
            t.ipv6::INTEGER AS ipv6,
            LENGTH(t.caps) AS caps_len,
            LENGTH(t.transport) AS transport_len
        FROM timeseries_router_stats t
        QUALIFY ROW_NUMBER() OVER (PARTITION BY router_hash ORDER BY timestamp DESC) = 1
    """).fetchall()

    router_hashes = []
    features = []

    for row in rows:
        router_hash, uptime, asn, ipv6, caps_len, transport_len = row

        router_hashes.append(router_hash)

        # Sanitize all fields
        uptime = uptime if uptime is not None else 0.0
        asn = asn if asn is not None else 0
        ipv6 = ipv6 if ipv6 is not None else 0
        caps_len = caps_len if caps_len is not None else 0
        transport_len = transport_len if transport_len is not None else 0

        features.append([
            float(uptime),
            float(asn),
            float(ipv6),
            float(caps_len),
            float(transport_len),
        ])

    X = np.array(features, dtype=float)

    # Replace NaN, inf, -inf with safe values
    X = np.nan_to_num(X, nan=0.0, posinf=0.0, neginf=0.0)

    return router_hashes, X


def write_clusters(conn, router_hashes, labels):
    ts = int(time.time() * 1000)

    for h, c in zip(router_hashes, labels):
        conn.execute("""
            INSERT OR REPLACE INTO router_clusters
            (router_hash, timestamp, cluster)
            VALUES (?, ?, ?)
        """, (h, ts, int(c)))

    conn.commit()


def run_router_clustering(n_clusters=8):
    conn = connect_db()
    ensure_cluster_table(conn)

    print("[*] Loading router features...")
    router_hashes, X = load_feature_matrix(conn)

    if len(X) == 0:
        print("[WARN] No router features available.")
        return

    # If all rows are identical, clustering is meaningless
    if np.all(X == X[0]):
        print("[WARN] All router features identical. Assigning cluster 0 to all.")
        labels = np.zeros(len(X), dtype=int)
        write_clusters(conn, router_hashes, labels)
        return

    # If fewer routers than clusters, reduce k
    if len(X) < n_clusters:
        print(f"[WARN] Only {len(X)} routers available. Reducing k to {len(X)}.")
        n_clusters = len(X)

    print("[*] Normalizing features...")
    scaler = StandardScaler()

    # Guard against zero-variance columns
    try:
        X_scaled = scaler.fit_transform(X)
    except Exception:
        print("[WARN] Zero variance detected. Using raw features.")
        X_scaled = X

    print(f"[*] Running KMeans clustering (k={n_clusters})...")
    kmeans = KMeans(n_clusters=n_clusters, n_init=10, random_state=42)

    try:
        labels = kmeans.fit_predict(X_scaled)
    except Exception:
        print("[WARN] KMeans failed on scaled data. Retrying with raw features.")
        labels = kmeans.fit_predict(X)

    print("[*] Writing cluster assignments...")
    write_clusters(conn, router_hashes, labels)

    print("[*] Router clustering complete.")

