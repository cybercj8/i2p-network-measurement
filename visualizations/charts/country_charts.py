import os
import duckdb
import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt

DB_PATH = "data/i2p.duckdb"
CHART_DIR = os.path.join(os.getcwd(), "visualizations", "charts")
os.makedirs(CHART_DIR, exist_ok=True)


def load_df(query):
    con = duckdb.connect(DB_PATH, read_only=True)
    df = con.execute(query).df()
    con.close()
    return df


# ---------------------------------------------------------
# 1. Country router count distribution (top 20)
# ---------------------------------------------------------
def country_router_distribution():
    df = load_df("""
        SELECT country_geo, COUNT(*) AS router_count
        FROM enriched_router_data
        WHERE country_geo IS NOT NULL
        GROUP BY country_geo
        ORDER BY router_count DESC
        LIMIT 20
    """)

    plt.figure(figsize=(10, 6))
    plt.barh(df["country_geo"][::-1], df["router_count"][::-1], color="steelblue")
    plt.title("Top 20 Countries by Router Count")
    plt.xlabel("Router Count")
    plt.ylabel("Country")
    plt.tight_layout()
    plt.savefig(os.path.join(CHART_DIR, "country_router_distribution.png"))
    plt.close()


# ---------------------------------------------------------
# 2. Country avg risk distribution (top 20)
# ---------------------------------------------------------
def country_risk_distribution():
    df = load_df("""
        SELECT e.country_geo, AVG(f.risk_score) AS avg_risk
        FROM enriched_router_data e
        JOIN router_features f USING (router_hash)
        WHERE e.country_geo IS NOT NULL
        GROUP BY e.country_geo
        ORDER BY avg_risk DESC
        LIMIT 20
    """)

    plt.figure(figsize=(10, 6))
    plt.barh(df["country_geo"][::-1], df["avg_risk"][::-1], color="darkred")
    plt.title("Top 20 Countries by Avg Risk Score")
    plt.xlabel("Avg Risk Score")
    plt.ylabel("Country")
    plt.tight_layout()
    plt.savefig(os.path.join(CHART_DIR, "country_risk_distribution.png"))
    plt.close()


# ---------------------------------------------------------
# 3. Country avg anomaly distribution (top 20)
# ---------------------------------------------------------
def country_anomaly_distribution():
    df = load_df("""
        SELECT e.country_geo, AVG(f.anomaly_score) AS avg_anomaly
        FROM enriched_router_data e
        JOIN router_features f USING (router_hash)
        WHERE e.country_geo IS NOT NULL
        GROUP BY e.country_geo
        ORDER BY avg_anomaly ASC
        LIMIT 20
    """)

    plt.figure(figsize=(10, 6))
    plt.barh(df["country_geo"][::-1], df["avg_anomaly"][::-1], color="purple")
    plt.title("Top 20 Most Anomalous Countries (Avg IsolationForest Score)")
    plt.xlabel("Avg Anomaly Score (lower = more anomalous)")
    plt.ylabel("Country")
    plt.tight_layout()
    plt.savefig(os.path.join(CHART_DIR, "country_anomaly_distribution.png"))
    plt.close()


# ---------------------------------------------------------
# 4. Country avg suspiciousness distribution (top 20)
# ---------------------------------------------------------
def country_suspicious_distribution():
    df = load_df("""
        SELECT e.country_geo, AVG(f.suspiciousness) AS avg_suspiciousness
        FROM enriched_router_data e
        JOIN router_features f USING (router_hash)
        WHERE e.country_geo IS NOT NULL
        GROUP BY e.country_geo
        ORDER BY avg_suspiciousness DESC
        LIMIT 20
    """)

    plt.figure(figsize=(10, 6))
    plt.barh(df["country_geo"][::-1], df["avg_suspiciousness"][::-1], color="orange")
    plt.title("Top 20 Countries by Avg Suspiciousness")
    plt.xlabel("Avg Suspiciousness")
    plt.ylabel("Country")
    plt.tight_layout()
    plt.savefig(os.path.join(CHART_DIR, "country_suspicious_distribution.png"))
    plt.close()


def run_country_charts():
    print("[CHARTS] Building country router distribution…")
    country_router_distribution()

    print("[CHARTS] Building country risk distribution…")
    country_risk_distribution()

    print("[CHARTS] Building country anomaly distribution…")
    country_anomaly_distribution()

    print("[CHARTS] Building country suspicious distribution…")
    country_suspicious_distribution()

    print("[CHARTS] Country charts complete.")


if __name__ == "__main__":
    run_country_charts()
