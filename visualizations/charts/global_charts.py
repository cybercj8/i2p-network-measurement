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
# 1. Global router count over time (snapshot cycles)
# ---------------------------------------------------------
def global_router_counts():
    df = load_df("""
        SELECT date_trunc('hour', timestamp) AS bucket, COUNT(DISTINCT router_hash) AS count
        FROM timeseries_router_stats
        GROUP BY bucket
        ORDER BY bucket
    """)

    plt.figure(figsize=(10, 5))
    plt.plot(df["bucket"], df["count"], marker="o", color="blue")
    plt.title("Global Router Count Over Time")
    plt.xlabel("Time")
    plt.ylabel("Distinct Routers Observed")
    plt.xticks(rotation=30)
    plt.tight_layout()
    plt.savefig(os.path.join(CHART_DIR, "global_router_counts.png"))
    plt.close()


# ---------------------------------------------------------
# 2. Global ASN counts (top 20)
# ---------------------------------------------------------
def global_asn_counts():
    df = load_df("""
        SELECT asn, COUNT(*) AS router_count
        FROM enriched_router_data
        WHERE asn IS NOT NULL
        GROUP BY asn
        ORDER BY router_count DESC
        LIMIT 20
    """)

    plt.figure(figsize=(10, 6))
    plt.barh(df["asn"].astype(str)[::-1], df["router_count"][::-1], color="darkgreen")
    plt.title("Global Top 20 ASNs by Router Count")
    plt.xlabel("Router Count")
    plt.ylabel("ASN")
    plt.tight_layout()
    plt.savefig(os.path.join(CHART_DIR, "global_asn_counts.png"))
    plt.close()


# ---------------------------------------------------------
# 3. Global risk score distribution
# ---------------------------------------------------------
def global_risk():
    df = load_df("SELECT risk_score FROM router_features WHERE risk_score IS NOT NULL")
    plt.figure(figsize=(8, 5))
    plt.hist(df["risk_score"], bins=_safe_bins(df["risk_score"]), color="darkred", edgecolor="black")
    plt.title("Global Risk Score Distribution")
    plt.xlabel("Risk Score")
    plt.ylabel("Count")
    plt.tight_layout()
    plt.savefig(os.path.join(CHART_DIR, "global_risk.png"))
    plt.close()


# ---------------------------------------------------------
# 4. Global suspiciousness distribution
# ---------------------------------------------------------
def global_suspicious():
    df = load_df("SELECT suspiciousness FROM router_features WHERE suspiciousness IS NOT NULL")
    plt.figure(figsize=(8, 5))
    plt.hist(df["suspiciousness"], bins=_safe_bins(df["suspiciousness"]), color="orange", edgecolor="black")
    plt.title("Global Suspiciousness Distribution")
    plt.xlabel("Suspiciousness")
    plt.ylabel("Count")
    plt.tight_layout()
    plt.savefig(os.path.join(CHART_DIR, "global_suspicious.png"))
    plt.close()


def metric_correlation_heatmap():
    """
    Pairwise Pearson correlation across every computed router metric
    (risk, anomaly, suspiciousness, entropy, periodicity, stability,
    cluster distance, uptime) -- a multicollinearity check. Dark cells off
    the diagonal mean two supposedly-separate metrics are largely
    restating the same information, not two independent signals.
    """
    import numpy as np

    cols = ["risk_score", "anomaly_score", "suspiciousness", "entropy",
            "periodicity", "stability", "cluster_distance", "uptime_hours"]
    labels = ["Risk", "Anomaly", "Suspicious", "Entropy",
              "Periodicity", "Stability", "Clust.Dist", "Uptime"]

    df = load_df(f"""
        SELECT {", ".join(cols)}
        FROM router_features f
        LEFT JOIN router_behavior b USING (router_hash)
    """)

    corr = df.corr(method="pearson", min_periods=30)
    if corr.isna().all().all():
        print("[CHARTS] Not enough data yet for correlation heatmap, skipping.")
        return

    fig, ax = plt.subplots(figsize=(8, 7))
    im = ax.imshow(corr.values, cmap="RdBu_r", vmin=-1, vmax=1)
    ax.set_xticks(range(len(labels)))
    ax.set_yticks(range(len(labels)))
    ax.set_xticklabels(labels, rotation=45, ha="right")
    ax.set_yticklabels(labels)

    for i in range(len(labels)):
        for j in range(len(labels)):
            val = corr.values[i, j]
            if not np.isnan(val):
                ax.text(j, i, f"{val:.2f}", ha="center", va="center",
                        color="white" if abs(val) > 0.5 else "black", fontsize=8)

    plt.colorbar(im, ax=ax, label="Pearson r")
    plt.title("Cross-Metric Correlation (Multicollinearity Check)")
    plt.tight_layout()
    plt.savefig(os.path.join(CHART_DIR, "metric_correlation_heatmap.png"))
    plt.close()


def run_global_charts():
    print("[CHARTS] Building global router counts…")
    global_router_counts()

    print("[CHARTS] Building global ASN counts…")
    global_asn_counts()

    print("[CHARTS] Building global risk distribution…")
    global_risk()

    print("[CHARTS] Building global suspicious distribution…")
    global_suspicious()

    print("[CHARTS] Building metric correlation heatmap…")
    metric_correlation_heatmap()

    print("[CHARTS] Global charts complete.")


if __name__ == "__main__":
    run_global_charts()
