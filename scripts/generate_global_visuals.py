import os
import duckdb
import matplotlib.pyplot as plt
import pandas as pd

# ---------------------------------------------------------
# PATHS
# ---------------------------------------------------------
BASE_DIR = os.getcwd()
DB_PATH = os.path.join(BASE_DIR, "data", "i2p.duckdb")

CHART_DIR = os.path.join(BASE_DIR, "visualizations", "charts")
MAP_DIR = os.path.join(BASE_DIR, "visualizations", "maps")

os.makedirs(CHART_DIR, exist_ok=True)
os.makedirs(MAP_DIR, exist_ok=True)

# ---------------------------------------------------------
# DB CONNECTION
# ---------------------------------------------------------
def get_conn():
    return duckdb.connect(DB_PATH, read_only=True)

# ---------------------------------------------------------
# GLOBAL RISK DISTRIBUTION
# ---------------------------------------------------------
def generate_global_risk_chart():
    conn = get_conn()
    df = conn.execute("""
        SELECT risk_score
        FROM router_risk_scores
        WHERE risk_score IS NOT NULL
    """).df()

    if df.empty:
        print("[WARN] No risk data found.")
        return

    plt.figure(figsize=(10,5))
    plt.hist(df["risk_score"], bins=50, color="red")
    plt.title("Global Risk Score Distribution")
    plt.xlabel("Risk Score")
    plt.ylabel("Count")
    plt.tight_layout()
    plt.savefig(os.path.join(CHART_DIR, "global_risk.png"))
    plt.close()

# ---------------------------------------------------------
# GLOBAL ANOMALY DISTRIBUTION
# ---------------------------------------------------------
def generate_global_anomaly_chart():
    conn = get_conn()
    df = conn.execute("""
        SELECT anomaly_score
        FROM router_anomalies
        WHERE anomaly_score IS NOT NULL
    """).df()

    if df.empty:
        print("[WARN] No anomaly data found.")
        return

    plt.figure(figsize=(10,5))
    plt.hist(df["anomaly_score"], bins=50, color="blue")
    plt.title("Global Anomaly Score Distribution")
    plt.xlabel("Anomaly Score")
    plt.ylabel("Count")
    plt.tight_layout()
    plt.savefig(os.path.join(CHART_DIR, "global_anomaly.png"))
    plt.close()

# ---------------------------------------------------------
# GLOBAL ROUTER COUNT OVER TIME
# ---------------------------------------------------------
def generate_global_router_count_chart():
    conn = get_conn()
    df = conn.execute("""
        SELECT timestamp, COUNT(*) AS total
        FROM timeseries_router_stats
        GROUP BY timestamp
        ORDER BY timestamp
    """).df()

    if df.empty:
        print("[WARN] No timeseries data found.")
        return

    plt.figure(figsize=(12,5))
    plt.plot(df["timestamp"], df["total"], color="green")
    plt.title("Global Router Count Over Time")
    plt.xlabel("Timestamp")
    plt.ylabel("Total Routers")
    plt.xticks(rotation=45)
    plt.tight_layout()
    plt.savefig(os.path.join(CHART_DIR, "global_router_counts.png"))
    plt.close()

# ---------------------------------------------------------
# GLOBAL ASN COUNT OVER TIME
# ---------------------------------------------------------
def generate_global_asn_count_chart():
    conn = get_conn()
    df = conn.execute("""
        SELECT timestamp, COUNT(DISTINCT asn) AS asn_count
        FROM router_asn_map
        GROUP BY timestamp
        ORDER BY timestamp
    """).df()

    if df.empty:
        print("[WARN] No ASN map data found.")
        return

    plt.figure(figsize=(12,5))
    plt.plot(df["timestamp"], df["asn_count"], color="purple")
    plt.title("Global ASN Count Over Time")
    plt.xlabel("Timestamp")
    plt.ylabel("Unique ASNs")
    plt.xticks(rotation=45)
    plt.tight_layout()
    plt.savefig(os.path.join(CHART_DIR, "global_asn_counts.png"))
    plt.close()

# ---------------------------------------------------------
# GLOBAL MAP PLACEHOLDER
# ---------------------------------------------------------
def generate_global_map():
    plt.figure(figsize=(12,6))
    plt.text(0.5, 0.5, "Global Map Placeholder", ha="center", va="center", fontsize=24)
    plt.title("Global Router Distribution Map")
    plt.axis("off")
    plt.savefig(os.path.join(MAP_DIR, "global_map.png"))
    plt.close()

# ---------------------------------------------------------
# MAIN
# ---------------------------------------------------------
def main():
    print("Generating global visualizations...")

    generate_global_risk_chart()
    generate_global_anomaly_chart()
    generate_global_router_count_chart()
    generate_global_asn_count_chart()
    generate_global_map()

    print("Done.")

if __name__ == "__main__":
    main()

