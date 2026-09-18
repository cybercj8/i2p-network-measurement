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
# 1. Cluster size distribution
# ---------------------------------------------------------
def cluster_distribution():
    df = load_df("""
        SELECT cluster_id, COUNT(*) AS count
        FROM router_behavior
        GROUP BY cluster_id
        ORDER BY cluster_id
    """)

    plt.figure(figsize=(9, 5))
    plt.bar(df["cluster_id"].astype(str), df["count"], color="steelblue")
    plt.title("Router Count per Cluster")
    plt.xlabel("Cluster ID")
    plt.ylabel("Router Count")
    plt.tight_layout()
    plt.savefig(os.path.join(CHART_DIR, "cluster_distribution.png"))
    plt.close()


# ---------------------------------------------------------
# 2. Cluster distance distribution
# ---------------------------------------------------------
def cluster_distance_distribution():
    df = load_df("SELECT cluster_distance FROM router_behavior WHERE cluster_distance IS NOT NULL")
    plt.figure(figsize=(8, 5))
    plt.hist(df["cluster_distance"], bins=_safe_bins(df["cluster_distance"]), color="darkred", edgecolor="black")
    plt.title("Cluster Distance Distribution")
    plt.xlabel("Distance from Cluster Centroid")
    plt.ylabel("Count")
    plt.tight_layout()
    plt.savefig(os.path.join(CHART_DIR, "cluster_distance_distribution.png"))
    plt.close()


# ---------------------------------------------------------
# 3. Avg risk per cluster
# ---------------------------------------------------------
def cluster_risk_distribution():
    df = load_df("""
        SELECT b.cluster_id, AVG(f.risk_score) AS avg_risk
        FROM router_behavior b
        JOIN router_features f USING (router_hash)
        GROUP BY b.cluster_id
        ORDER BY b.cluster_id
    """)

    plt.figure(figsize=(9, 5))
    bars = plt.bar(df["cluster_id"].astype(str), df["avg_risk"], color="darkred")
    plt.title("Avg Risk Score per Cluster")
    plt.xlabel("Cluster ID")
    plt.ylabel("Avg Risk Score")

    # Same issue as the anomaly chart: a cluster with an avg risk score of
    # exactly (or very near) 0 produces a bar too small to see, which reads
    # as "no data" rather than "genuinely zero". Label every bar.
    for bar, val in zip(bars, df["avg_risk"]):
        offset = 0.002 if val >= 0 else -0.002
        va = "bottom" if val >= 0 else "top"
        plt.text(bar.get_x() + bar.get_width() / 2, val + offset, f"{val:.4f}",
                  ha="center", va=va, fontsize=7)

    plt.tight_layout()
    plt.savefig(os.path.join(CHART_DIR, "cluster_risk_distribution.png"))
    plt.close()


# ---------------------------------------------------------
# 4. Avg anomaly per cluster
# ---------------------------------------------------------
def cluster_anomaly_distribution():
    df = load_df("""
        SELECT b.cluster_id, AVG(f.anomaly_score) AS avg_anomaly
        FROM router_behavior b
        JOIN router_features f USING (router_hash)
        GROUP BY b.cluster_id
        ORDER BY b.cluster_id
    """)

    plt.figure(figsize=(9, 5))
    bars = plt.bar(df["cluster_id"].astype(str), df["avg_anomaly"], color="purple")
    plt.title("Avg Anomaly Score per Cluster")
    plt.xlabel("Cluster ID")
    plt.ylabel("Avg Anomaly Score")

    # Several clusters carry an avg anomaly score near zero -- close enough
    # to the axis that the bar itself is a sliver or invisible, which reads
    # as "no data" rather than "genuinely near-zero value". Label every bar
    # with its exact value so a near-zero cluster is still legible.
    #
    # anomaly_score in this project is bounded at (or extremely close to) 0
    # from above -- IsolationForest's normal-baseline routers land at
    # exactly 0, actual anomalies go negative, positive values essentially
    # don't occur. Since y=0 sits at the TOP of this chart's range, a label
    # placed "above" an exactly-zero bar collides with the title -- so a
    # value of exactly 0 needs to be treated like the surrounding negative
    # bars (label below), not like a genuine positive one. Hence `> 0`
    # here, not `>= 0`.
    for bar, val in zip(bars, df["avg_anomaly"]):
        offset = 0.002 if val > 0 else -0.002
        va = "bottom" if val > 0 else "top"
        plt.text(bar.get_x() + bar.get_width() / 2, val + offset, f"{val:.4f}",
                  ha="center", va=va, fontsize=7)

    plt.tight_layout()
    plt.savefig(os.path.join(CHART_DIR, "cluster_anomaly_distribution.png"))
    plt.close()


# ---------------------------------------------------------
# 5. Per-cluster detail charts (cluster_detail page)
# ---------------------------------------------------------
def per_cluster_detail_charts():
    """
    cluster_detail.html embeds three per-cluster charts:
    cluster_<id>_{risk,anomaly,behavior}.png -- scoped to just that
    cluster's own routers, not the cross-cluster comparison charts above.
    """
    con = duckdb.connect(DB_PATH, read_only=True)
    cluster_ids = con.execute("""
        SELECT DISTINCT cluster_id FROM router_behavior WHERE cluster_id IS NOT NULL
    """).df()["cluster_id"].tolist()

    for cid in cluster_ids:
        df = con.execute("""
            SELECT f.risk_score, f.anomaly_score, b.cluster_distance
            FROM router_behavior b
            JOIN router_features f USING (router_hash)
            WHERE b.cluster_id = ?
        """, [cid]).df()

        if df.empty:
            continue

        plt.figure(figsize=(7, 4))
        plt.hist(df["risk_score"].dropna(), bins=_safe_bins(df["risk_score"]), color="darkred", edgecolor="black")
        plt.title(f"Risk Score Distribution (Cluster {cid})")
        plt.xlabel("Risk Score")
        plt.ylabel("Count")
        plt.tight_layout()
        plt.savefig(os.path.join(CHART_DIR, f"cluster_{cid}_risk.png"))
        plt.close()

        plt.figure(figsize=(7, 4))
        plt.hist(df["anomaly_score"].dropna(), bins=_safe_bins(df["anomaly_score"]), color="purple", edgecolor="black")
        plt.title(f"Anomaly Score Distribution (Cluster {cid})")
        plt.xlabel("Anomaly Score")
        plt.ylabel("Count")
        plt.tight_layout()
        plt.savefig(os.path.join(CHART_DIR, f"cluster_{cid}_anomaly.png"))
        plt.close()

        plt.figure(figsize=(7, 4))
        plt.hist(df["cluster_distance"].dropna(), bins=_safe_bins(df["cluster_distance"]), color="steelblue", edgecolor="black")
        plt.title(f"Behavioral Distance Distribution (Cluster {cid})")
        plt.xlabel("Distance from Cluster Centroid")
        plt.ylabel("Count")
        plt.tight_layout()
        plt.savefig(os.path.join(CHART_DIR, f"cluster_{cid}_behavior.png"))
        plt.close()

    con.close()


def run_cluster_charts():
    print("[CHARTS] Building cluster distribution…")
    cluster_distribution()

    print("[CHARTS] Building cluster distance distribution…")
    cluster_distance_distribution()

    print("[CHARTS] Building cluster risk distribution…")
    cluster_risk_distribution()

    print("[CHARTS] Building cluster anomaly distribution…")
    cluster_anomaly_distribution()

    print("[CHARTS] Building per-cluster detail charts…")
    per_cluster_detail_charts()

    print("[CHARTS] Cluster charts complete.")


if __name__ == "__main__":
    run_cluster_charts()
