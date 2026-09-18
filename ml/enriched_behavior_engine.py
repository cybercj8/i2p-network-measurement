import duckdb
import pandas as pd
import numpy as np
import time
from sklearn.cluster import MiniBatchKMeans
from sklearn.preprocessing import StandardScaler

DB_PATH = "data/i2p.duckdb"
N_CLUSTERS = 12


def connect():
    return duckdb.connect(DB_PATH)


# ------------------------------------------------------------
# Load enriched feature matrix using ALL capability flags
# ------------------------------------------------------------
def load_enriched_matrix(con):

    df = con.execute("""
        SELECT
            router_hash,

            -- Numeric features
            COALESCE(asn, 0) AS asn,
            COALESCE(asn_country, 0) AS asn_country,
            COALESCE(latitude, 0) AS latitude,
            COALESCE(longitude, 0) AS longitude,
            COALESCE(version_major, 0) AS version_major,
            COALESCE(version_minor, 0) AS version_minor,
            COALESCE(version_patch, 0) AS version_patch,
            COALESCE(version_build, 0) AS version_build,

            -- Capability flags (booleans → 0/1)
            CAST(is_floodfill_cap AS INTEGER) AS is_floodfill_cap,
            CAST(is_reachable AS INTEGER) AS is_reachable,
            CAST(is_hidden AS INTEGER) AS is_hidden,
            CAST(is_unreachable AS INTEGER) AS is_unreachable,
            CAST(is_congested_medium AS INTEGER) AS is_congested_medium,
            CAST(is_congested_high AS INTEGER) AS is_congested_high,
            CAST(is_rejecting_tunnels AS INTEGER) AS is_rejecting_tunnels,
            CAST(supports_ipv6 AS INTEGER) AS supports_ipv6,
            CAST(supports_ntcp2 AS INTEGER) AS supports_ntcp2,
            CAST(supports_ntcp AS INTEGER) AS supports_ntcp,
            CAST(supports_ssu2 AS INTEGER) AS supports_ssu2,
            CAST(supports_ssu AS INTEGER) AS supports_ssu,

            -- Bandwidth flags
            CAST(bw_low AS INTEGER) AS bw_low,
            CAST(bw_mid AS INTEGER) AS bw_mid,
            CAST(bw_high AS INTEGER) AS bw_high,
            CAST(bw_unlimited AS INTEGER) AS bw_unlimited

        FROM enriched_router_data
    """).df()

    if df.empty:
        return None, None

    feature_cols = [
        "asn", "asn_country", "latitude", "longitude",
        "version_major", "version_minor", "version_patch", "version_build",
        "is_floodfill_cap", "is_reachable", "is_hidden", "is_unreachable",
        "is_congested_medium", "is_congested_high", "is_rejecting_tunnels",
        "supports_ipv6", "supports_ntcp2", "supports_ntcp",
        "supports_ssu2", "supports_ssu",
        "bw_low", "bw_mid", "bw_high", "bw_unlimited"
    ]

    X = df[feature_cols].fillna(0).astype(float).values
    return df["router_hash"].tolist(), X


# ------------------------------------------------------------
# Compute entropy, periodicity, stability
# ------------------------------------------------------------
# These used to be std/mean/count-of-nonzero computed ACROSS a single
# router's mixed feature vector (ASN, lat/lon, version, capability
# booleans) -- a cross-sectional statistic with no time dimension at all,
# despite the names implying temporal behavior. That formula also meant
# periodicity (mean) and stability (count-of-nonzero) necessarily moved
# together, and entropy (std) moved opposite them, purely as a mechanical
# consequence of how those three statistics relate on the same vector --
# not a real behavioral signal. Replaced with genuine temporal metrics
# computed from each router's actual observation history across pipeline
# runs (timeseries_router_stats), which is what these names should mean:
#   stability    = how many times this router has actually been observed
#   periodicity  = how regularly-spaced those observations are
#                  (1 / (1 + coefficient of variation of intervals);
#                  1.0 = perfectly regular, -> 0 as intervals get erratic)
#   entropy      = Shannon entropy of the interval distribution
#                  (higher = more unpredictable timing)
# Routers with too little history for a metric to be meaningful get NULL
# instead of a fabricated value.
def compute_temporal_behavior_metrics(con, router_hashes):
    rows = con.execute("""
        SELECT router_hash, timestamp
        FROM timeseries_router_stats
        WHERE router_hash IN (SELECT UNNEST(?))
        ORDER BY router_hash, timestamp
    """, [router_hashes]).fetchall()

    history = {}
    for router_hash, ts in rows:
        history.setdefault(router_hash, []).append(ts)

    stability_out, periodicity_out, entropy_out = [], [], []

    for router_hash in router_hashes:
        timestamps = history.get(router_hash, [])
        observation_count = len(timestamps)
        stability_out.append(float(observation_count))

        if observation_count < 3:
            periodicity_out.append(None)
            entropy_out.append(None)
            continue

        intervals_min = np.array([
            (timestamps[i] - timestamps[i - 1]).total_seconds() / 60.0
            for i in range(1, len(timestamps))
        ])
        intervals_min = intervals_min[intervals_min > 0]

        if len(intervals_min) < 2:
            periodicity_out.append(None)
            entropy_out.append(None)
            continue

        mean_iv = float(np.mean(intervals_min))
        cv = float(np.std(intervals_min) / mean_iv) if mean_iv > 0 else None
        periodicity_out.append(1.0 / (1.0 + cv) if cv is not None else None)

        n_bins = min(5, len(set(np.round(intervals_min, 1))))
        if n_bins < 2:
            entropy_out.append(0.0)  # all intervals identical -> zero disorder
        else:
            counts, _ = np.histogram(intervals_min, bins=n_bins)
            counts = counts[counts > 0]
            probs = counts / counts.sum()
            entropy_out.append(float(-np.sum(probs * np.log2(probs))))

    return np.array(entropy_out, dtype=object), np.array(periodicity_out, dtype=object), np.array(stability_out)


# ------------------------------------------------------------
# Main behavior engine
# ------------------------------------------------------------
def run_enriched_behavior_engine():
    con = connect()

    print("[BEHAVIOR] Loading enriched feature matrix…")
    router_hashes, X = load_enriched_matrix(con)

    if X is None or len(X) == 0:
        print("[BEHAVIOR] No enriched data available.")
        return

    print("[BEHAVIOR] Scaling features…")
    scaler = StandardScaler()
    X_scaled = scaler.fit_transform(X)

    print(f"[BEHAVIOR] Running MiniBatchKMeans (k={N_CLUSTERS})…")
    kmeans = MiniBatchKMeans(
        n_clusters=N_CLUSTERS,
        batch_size=50_000,
        random_state=42
    )
    kmeans.fit(X_scaled)

    print("[BEHAVIOR] Assigning clusters…")
    clusters = kmeans.predict(X_scaled)
    centers = kmeans.cluster_centers_
    distances = np.linalg.norm(X_scaled - centers[clusters], axis=1)

    print("[BEHAVIOR] Computing temporal behavior metrics…")
    entropy, periodicity, stability = compute_temporal_behavior_metrics(con, router_hashes)

    ts = int(time.time() * 1000)

    # ------------------------------------------------------------
    # Write router_behavior
    # ------------------------------------------------------------
    print("[BEHAVIOR] Writing router_behavior…")
    con.execute("DROP TABLE IF EXISTS router_behavior")
    con.execute("""
        CREATE TABLE router_behavior (
            router_hash TEXT,
            cluster_id INTEGER,
            cluster_distance DOUBLE,
            entropy DOUBLE,
            periodicity DOUBLE,
            stability DOUBLE
        )
    """)

    df = pd.DataFrame({
        "router_hash": router_hashes,
        "cluster_id": clusters,
        "cluster_distance": distances,
        "entropy": entropy,
        "periodicity": periodicity,
        "stability": stability
    })

    con.register("batch", df)
    con.execute("INSERT INTO router_behavior SELECT * FROM batch")

    # ------------------------------------------------------------
    # Write router_behavior_history
    # ------------------------------------------------------------
    print("[BEHAVIOR] Writing router_behavior_history…")
    con.execute("""
        CREATE TABLE IF NOT EXISTS router_behavior_history (
            router_hash TEXT,
            timestamp BIGINT,
            cluster_id INTEGER,
            entropy DOUBLE,
            periodicity DOUBLE,
            stability DOUBLE
        )
    """)

    df_hist = pd.DataFrame({
        "router_hash": router_hashes,
        "timestamp": ts,
        "cluster_id": clusters,
        "entropy": entropy,
        "periodicity": periodicity,
        "stability": stability
    })

    con.register("hist", df_hist)
    con.execute("INSERT INTO router_behavior_history SELECT * FROM hist")

    print("[BEHAVIOR] Enriched behavior engine complete.")

