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


def _safe_bins(data, max_bins=20):
    data = data.dropna()
    if len(data) == 0:
        return 1
    if (data.max() - data.min()) <= 1e-9:
        return 1
    return min(max_bins, data.nunique())


# ---------------------------------------------------------
# 1. Suspiciousness distribution
# ---------------------------------------------------------
def suspicious_distribution():
    df = load_df("SELECT suspiciousness FROM router_features WHERE suspiciousness IS NOT NULL")
    plt.figure(figsize=(8, 5))
    plt.hist(df["suspiciousness"], bins=_safe_bins(df["suspiciousness"]), color="orange", edgecolor="black")
    plt.title("Suspiciousness Distribution")
    plt.xlabel("Suspiciousness")
    plt.ylabel("Count")
    plt.tight_layout()
    plt.savefig(os.path.join(CHART_DIR, "suspicious_distribution.png"))
    plt.close()


# ---------------------------------------------------------
# 2. Suspiciousness vs behavioral (cluster) distance
# ---------------------------------------------------------
def suspicious_behavior_overlay():
    df = load_df("""
        SELECT f.suspiciousness, b.cluster_distance
        FROM router_features f
        JOIN router_behavior b USING (router_hash)
        WHERE f.suspiciousness IS NOT NULL AND b.cluster_distance IS NOT NULL
    """)

    plt.figure(figsize=(8, 6))
    plt.scatter(df["cluster_distance"], df["suspiciousness"], alpha=0.4, s=10, color="orange")
    plt.title("Suspiciousness vs Behavioral Cluster Distance")
    plt.xlabel("Cluster Distance")
    plt.ylabel("Suspiciousness")
    plt.tight_layout()
    plt.savefig(os.path.join(CHART_DIR, "suspicious_behavior_overlay.png"))
    plt.close()


# ---------------------------------------------------------
# 3. Suspiciousness vs risk score
# ---------------------------------------------------------
def suspicious_risk_overlay():
    df = load_df("""
        SELECT suspiciousness, risk_score
        FROM router_features
        WHERE suspiciousness IS NOT NULL AND risk_score IS NOT NULL
    """)

    plt.figure(figsize=(8, 6))
    plt.scatter(df["risk_score"], df["suspiciousness"], alpha=0.4, s=10, color="darkred")
    plt.title("Suspiciousness vs Risk Score")
    plt.xlabel("Risk Score")
    plt.ylabel("Suspiciousness")
    plt.tight_layout()
    plt.savefig(os.path.join(CHART_DIR, "suspicious_risk_overlay.png"))
    plt.close()


def run_suspicious_charts():
    print("[CHARTS] Building suspicious distribution…")
    suspicious_distribution()

    print("[CHARTS] Building suspicious/behavior overlay…")
    suspicious_behavior_overlay()

    print("[CHARTS] Building suspicious/risk overlay…")
    suspicious_risk_overlay()

    print("[CHARTS] Suspicious charts complete.")


if __name__ == "__main__":
    run_suspicious_charts()
