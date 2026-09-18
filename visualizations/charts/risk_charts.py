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
# 1. Risk score distribution
# ---------------------------------------------------------
def risk_distribution():
    df = load_df("SELECT risk_score FROM router_features WHERE risk_score IS NOT NULL")
    plt.figure(figsize=(8, 5))
    plt.hist(df["risk_score"], bins=_safe_bins(df["risk_score"]), color="darkred", edgecolor="black")
    plt.title("Risk Score Distribution")
    plt.xlabel("Risk Score")
    plt.ylabel("Count")
    plt.tight_layout()
    plt.savefig(os.path.join(CHART_DIR, "risk_distribution.png"))
    plt.close()


# ---------------------------------------------------------
# 2. Risk vs anomaly overlay
# ---------------------------------------------------------
def risk_anomaly_overlay():
    df = load_df("""
        SELECT risk_score, anomaly_score
        FROM router_features
        WHERE risk_score IS NOT NULL AND anomaly_score IS NOT NULL
    """)

    plt.figure(figsize=(8, 6))
    plt.scatter(df["risk_score"], df["anomaly_score"], alpha=0.4, s=10, color="purple")
    plt.title("Risk Score vs Anomaly Score")
    plt.xlabel("Risk Score")
    plt.ylabel("Anomaly Score")
    plt.tight_layout()
    plt.savefig(os.path.join(CHART_DIR, "risk_anomaly_overlay.png"))
    plt.close()


# ---------------------------------------------------------
# 3. Risk vs suspiciousness overlay
# ---------------------------------------------------------
def risk_suspicious_overlay():
    df = load_df("""
        SELECT risk_score, suspiciousness
        FROM router_features
        WHERE risk_score IS NOT NULL AND suspiciousness IS NOT NULL
    """)

    plt.figure(figsize=(8, 6))
    plt.scatter(df["risk_score"], df["suspiciousness"], alpha=0.4, s=10, color="orange")
    plt.title("Risk Score vs Suspiciousness")
    plt.xlabel("Risk Score")
    plt.ylabel("Suspiciousness")
    plt.tight_layout()
    plt.savefig(os.path.join(CHART_DIR, "risk_suspicious_overlay.png"))
    plt.close()


def run_risk_charts():
    print("[CHARTS] Building risk distribution…")
    risk_distribution()

    print("[CHARTS] Building risk/anomaly overlay…")
    risk_anomaly_overlay()

    print("[CHARTS] Building risk/suspicious overlay…")
    risk_suspicious_overlay()

    print("[CHARTS] Risk charts complete.")


if __name__ == "__main__":
    run_risk_charts()
