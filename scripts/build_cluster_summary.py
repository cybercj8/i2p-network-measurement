import duckdb

DB = "data/i2p.duckdb"

def build_cluster_summary():
    print(">>> ENTERED build_cluster_summary()")   # DEBUG

    con = duckdb.connect(DB)
    print(">>> Connected to DB")                  # DEBUG

    # ---------------------------------------------------------
    # DROP OLD OBJECT (VIEW OR TABLE)
    # ---------------------------------------------------------
    try:
        con.execute("DROP VIEW IF EXISTS cluster_summary")
        print(">>> Dropped old VIEW (if existed)")   # DEBUG
    except Exception as e:
        print(">>> VIEW DROP ERROR:", e)

    try:
        con.execute("DROP TABLE IF EXISTS cluster_summary")
        print(">>> Dropped old TABLE (if existed)")  # DEBUG
    except Exception as e:
        print(">>> TABLE DROP ERROR:", e)

    # ---------------------------------------------------------
    # RECREATE CLUSTER SUMMARY TABLE
    # ---------------------------------------------------------
    try:
        con.execute("""
            CREATE TABLE cluster_summary AS
            SELECT
                b.cluster_id,
                COUNT(*) AS count,
                AVG(r.risk_score) AS avg_risk,
                AVG(s.suspiciousness) AS avg_suspiciousness,
                AVG(b.entropy) AS avg_entropy,
                AVG(b.periodicity) AS avg_periodicity,
                AVG(b.uptime_slope) AS avg_uptime_slope,
                AVG(a.anomaly_score) AS avg_anomaly_score,
                AVG(b.cluster_distance) AS avg_cluster_distance
            FROM router_behavior b
            LEFT JOIN router_risk_scores r USING (router_hash)
            LEFT JOIN suspicious_routers s USING (router_hash)
            LEFT JOIN router_anomalies a USING (router_hash)
            GROUP BY b.cluster_id
            ORDER BY b.cluster_id
        """)
        print(">>> SQL executed")                 # DEBUG
    except Exception as e:
        print(">>> SQL ERROR:", e)

    con.close()
    print("[CLUSTER SUMMARY] Completed successfully.")


if __name__ == "__main__":
    build_cluster_summary()

