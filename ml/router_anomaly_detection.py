import duckdb
import numpy as np
from sklearn.ensemble import IsolationForest
import time

DB_PATH = "data/i2p.duckdb"


def connect_db():
    return duckdb.connect(DB_PATH)


def ensure_anomaly_table(conn):
    conn.execute("""
    CREATE TABLE IF NOT EXISTS router_anomalies (
        router_hash TEXT,
        ts BIGINT,                     -- FIXED: renamed from 'timestamp'
        anomaly_score DOUBLE,
        reason TEXT,
        PRIMARY KEY (router_hash, ts)  -- FIXED: updated PK
    )
    """)
    conn.commit()


def load_feature_matrix(conn):
    rows = conn.execute("""
        SELECT
            t.router_hash,
            t.uptime_hours,
            t.asn,
            t.ipv6::INTEGER AS ipv6,
            LENGTH(t.caps) AS caps_len,
            LENGTH(t.transport) AS transport_len,
            COALESCE(a.cluster_id, -1) AS cluster_id
        FROM timeseries_router_stats t
        LEFT JOIN asn_info a
        ON t.asn = a.asn
        QUALIFY ROW_NUMBER() OVER (
            PARTITION BY t.router_hash ORDER BY t.timestamp DESC
        ) = 1
    """).fetchall()

    router_hashes = []
    features = []

    for row in rows:
        (
            router_hash, uptime, asn, ipv6,
            caps_len, transport_len, cluster_id
        ) = row

        router_hashes.append(router_hash)
        features.append([
            uptime,
            asn if asn is not None else 0,
            ipv6,
            caps_len,
            transport_len,
            cluster_id,
        ])

    return router_hashes, np.array(features, dtype=float)


def detect_rule_based_anomalies(conn):
    anomalies = []

    rows = conn.execute("""
        SELECT router_hash, COUNT(DISTINCT asn) AS asn_changes
        FROM router_asn_map
        GROUP BY router_hash
        HAVING asn_changes > 3
    """).fetchall()

    for router_hash, changes in rows:
        anomalies.append((router_hash, f"ASN hopping detected ({changes} changes)"))

    rows = conn.execute("""
        SELECT router_hash, COUNT(*) AS churn_events
        FROM router_churn
        GROUP BY router_hash
        HAVING churn_events > 10
    """).fetchall()

    for router_hash, events in rows:
        anomalies.append((router_hash, f"High churn frequency ({events} events)"))

    return anomalies


def write_anomaly(conn, router_hash, score, reason):
    ts_ms = int(time.time() * 1000)

    conn.execute("""
        INSERT OR REPLACE INTO router_anomalies
        (router_hash, ts, anomaly_score, reason)
        VALUES (?, ?, ?, ?)
    """, (
        router_hash,
        ts_ms,          # stays BIGINT
        None if score is None else float(score),
        reason
    ))
    conn.commit()


def run_router_anomaly_detection():
    conn = connect_db()
    ensure_anomaly_table(conn)

    # This function re-derives the *complete current* anomaly state from
    # scratch every run (both branches below scan the full router set, not
    # just what changed). Without this, ts (wall-clock at write time) makes
    # every row unique, so INSERT OR REPLACE never actually replaces anything --
    # each rerun just appends another duplicate row for the same still-true
    # condition, and router_features.anomaly_score (AVG'd from this table)
    # drifts further every time the pipeline runs.
    conn.execute("DELETE FROM router_anomalies")
    conn.commit()

    print("[*] Loading router features...")
    router_hashes, X = load_feature_matrix(conn)

    if len(X) < 10:
        print("[WARN] Not enough routers for anomaly detection.")
        return

    print("[*] Running Isolation Forest anomaly detection...")
    iso = IsolationForest(contamination=0.05, random_state=42)
    scores = iso.fit_predict(X)
    anomaly_scores = iso.decision_function(X)

    print("[*] Writing ML-based anomalies...")
    for h, score, label in zip(router_hashes, anomaly_scores, scores):
        if label == -1:
            write_anomaly(conn, h, score, "ML anomaly (IsolationForest)")

    print("[*] Running rule-based anomaly detection...")
    rule_anomalies = detect_rule_based_anomalies(conn)

    # Rule-based hits are a categorical flag ("this condition is true"), not a
    # continuous severity measurement -- writing a constant -1.0 here made it
    # get blended arithmetically with real IsolationForest decision_function
    # scores via AVG(anomaly_score), which is meaningless (11 churn events and
    # 18 churn events both became exactly -1.0, indistinguishable from a truly
    # severe ML-detected outlier). Store NULL instead so AVG() ignores it and
    # only real ML scores contribute to the numeric severity; the `reason`
    # text still carries the rule-based signal for display on /anomalies.
    for router_hash, reason in rule_anomalies:
        write_anomaly(conn, router_hash, None, reason)

    print("[*] Router anomaly detection complete.")

