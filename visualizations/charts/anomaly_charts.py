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
# 1. Anomaly score distribution
# ---------------------------------------------------------
def anomaly_distribution():
    df = load_df("SELECT anomaly_score FROM router_features WHERE anomaly_score IS NOT NULL")
    plt.figure(figsize=(8, 5))
    plt.hist(df["anomaly_score"], bins=_safe_bins(df["anomaly_score"]), color="purple", edgecolor="black")
    plt.title("Anomaly Score Distribution")
    plt.xlabel("Anomaly Score (IsolationForest, lower = more anomalous)")
    plt.ylabel("Count")
    plt.tight_layout()
    plt.savefig(os.path.join(CHART_DIR, "anomaly_distribution.png"))
    plt.close()


# ---------------------------------------------------------
# 2. Anomaly vs risk overlay
# ---------------------------------------------------------
def anomaly_risk_overlay():
    df = load_df("""
        SELECT anomaly_score, risk_score
        FROM router_features
        WHERE anomaly_score IS NOT NULL AND risk_score IS NOT NULL
    """)

    plt.figure(figsize=(8, 6))
    plt.scatter(df["anomaly_score"], df["risk_score"], alpha=0.4, s=10, color="darkred")
    plt.title("Anomaly Score vs Risk Score")
    plt.xlabel("Anomaly Score (lower = more anomalous)")
    plt.ylabel("Risk Score")
    plt.tight_layout()
    plt.savefig(os.path.join(CHART_DIR, "anomaly_risk_overlay.png"))
    plt.close()


# ---------------------------------------------------------
# 3. Anomaly vs suspiciousness overlay
# ---------------------------------------------------------
def anomaly_suspicious_overlay():
    df = load_df("""
        SELECT anomaly_score, suspiciousness
        FROM router_features
        WHERE anomaly_score IS NOT NULL AND suspiciousness IS NOT NULL
    """)

    plt.figure(figsize=(8, 6))
    plt.scatter(df["anomaly_score"], df["suspiciousness"], alpha=0.4, s=10, color="orange")
    plt.title("Anomaly Score vs Suspiciousness")
    plt.xlabel("Anomaly Score (lower = more anomalous)")
    plt.ylabel("Suspiciousness")
    plt.tight_layout()
    plt.savefig(os.path.join(CHART_DIR, "anomaly_suspicious_overlay.png"))
    plt.close()


def run_anomaly_charts():
    print("[CHARTS] Building anomaly distribution…")
    anomaly_distribution()

    print("[CHARTS] Building anomaly/risk overlay…")
    anomaly_risk_overlay()

    print("[CHARTS] Building anomaly/suspicious overlay…")
    anomaly_suspicious_overlay()

    print("[CHARTS] Anomaly charts complete.")


if __name__ == "__main__":
    run_anomaly_charts()
