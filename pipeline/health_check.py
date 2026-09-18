import duckdb
import sys
import time

DB_PATH = "data/i2p.duckdb"

# ------------------------------------------------------------
# Tables your UI, routes, charts, and maps depend on
# ------------------------------------------------------------
REQUIRED_TABLES = {
    "enriched_router_data": ["router_hash", "asn", "country_geo"],
    "router_behavior": ["router_hash", "entropy", "periodicity"],
    "router_anomalies": ["router_hash", "anomaly_score"],
    "router_suspicious": ["router_hash", "suspicious_score"],
    "router_locations": ["router_hash", "latitude", "longitude"],
    "router_location_history": ["router_hash", "timestamp"],
    "timeseries_router_stats": ["router_hash", "timestamp", "uptime_hours"],
    "router_churn": ["router_hash", "timestamp", "event"],
    "router_risk_scores": ["router_hash", "risk_score"],
    "suspicious_routers": ["router_hash", "suspiciousness"],
    "asn_info": ["asn", "risk_score", "router_count"],
    "cluster_summary": ["cluster_id", "avg_risk"],
}

# ------------------------------------------------------------
# Helper: check if table exists
# ------------------------------------------------------------
def table_exists(con, table):
    try:
        con.execute(f"SELECT * FROM {table} LIMIT 1")
        return True
    except:
        return False

# ------------------------------------------------------------
# Helper: check columns
# ------------------------------------------------------------
def check_columns(con, table, required_cols):
    try:
        cols = [c[0] for c in con.execute(f"PRAGMA table_info('{table}')").fetchall()]
        missing = [c for c in required_cols if c not in cols]
        return missing
    except:
        return required_cols  # if table unreadable, treat all as missing

# ------------------------------------------------------------
# Helper: check row count
# ------------------------------------------------------------
def row_count(con, table):
    try:
        return con.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0]
    except:
        return 0

# ------------------------------------------------------------
# MAIN HEALTH CHECK
# ------------------------------------------------------------
def run_health_check():
    print("\n====================================================")
    print("             HYBRID PIPELINE HEALTH CHECK")
    print("====================================================\n")

    con = duckdb.connect(DB_PATH)

    failures = 0

    for table, required_cols in REQUIRED_TABLES.items():
        print(f"[CHECK] Table: {table}")

        # Table existence
        if not table_exists(con, table):
            print(f"  ❌ MISSING TABLE: {table}")
            failures += 1
            continue
        else:
            print(f"  ✔ Exists")

        # Column check
        missing_cols = check_columns(con, table, required_cols)
        if missing_cols:
            print(f"  ❌ Missing columns: {missing_cols}")
            failures += 1
        else:
            print(f"  ✔ All required columns present")

        # Row count
        count = row_count(con, table)
        if count == 0:
            print(f"  ❌ Table is empty")
            failures += 1
        else:
            print(f"  ✔ Row count: {count}")

        print()

    print("====================================================")
    if failures == 0:
        print("  HEALTH CHECK PASSED — All required tables populated")
    else:
        print(f"  HEALTH CHECK FAILED — {failures} issues detected")
    print("====================================================\n")


if __name__ == "__main__":
    start = time.time()
    run_health_check()
    elapsed = time.time() - start
    print(f"[HEALTH CHECK] Completed in {elapsed:.2f}s ({time.strftime('%M:%S', time.gmtime(elapsed))})")

