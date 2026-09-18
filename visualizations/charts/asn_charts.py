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
# 1. ASN router count distribution (top 20)
# ---------------------------------------------------------
def asn_router_distribution():
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
    plt.title("Top 20 ASNs by Router Count")
    plt.xlabel("Router Count")
    plt.ylabel("ASN")
    plt.tight_layout()
    plt.savefig(os.path.join(CHART_DIR, "asn_router_distribution.png"))
    plt.close()


# ---------------------------------------------------------
# 2. ASN avg risk distribution (top 20)
# ---------------------------------------------------------
def asn_risk_distribution():
    df = load_df("""
        SELECT e.asn, AVG(f.risk_score) AS avg_risk
        FROM enriched_router_data e
        JOIN router_features f USING (router_hash)
        WHERE e.asn IS NOT NULL
        GROUP BY e.asn
        ORDER BY avg_risk DESC
        LIMIT 20
    """)

    plt.figure(figsize=(10, 6))
    plt.barh(df["asn"].astype(str)[::-1], df["avg_risk"][::-1], color="darkred")
    plt.title("Top 20 ASNs by Avg Risk Score")
    plt.xlabel("Avg Risk Score")
    plt.ylabel("ASN")
    plt.tight_layout()
    plt.savefig(os.path.join(CHART_DIR, "asn_risk_distribution.png"))
    plt.close()


# ---------------------------------------------------------
# 3. ASN avg anomaly distribution (top 20)
# ---------------------------------------------------------
def asn_anomaly_distribution():
    df = load_df("""
        SELECT e.asn, AVG(f.anomaly_score) AS avg_anomaly
        FROM enriched_router_data e
        JOIN router_features f USING (router_hash)
        WHERE e.asn IS NOT NULL
        GROUP BY e.asn
        ORDER BY avg_anomaly ASC
        LIMIT 20
    """)

    plt.figure(figsize=(10, 6))
    plt.barh(df["asn"].astype(str)[::-1], df["avg_anomaly"][::-1], color="purple")
    plt.title("Top 20 Most Anomalous ASNs (Avg IsolationForest Score)")
    plt.xlabel("Avg Anomaly Score (lower = more anomalous)")
    plt.ylabel("ASN")
    plt.tight_layout()
    plt.savefig(os.path.join(CHART_DIR, "asn_anomaly_distribution.png"))
    plt.close()


# ---------------------------------------------------------
# 4. ASN avg suspiciousness distribution (top 20)
# ---------------------------------------------------------
def asn_suspicious_distribution():
    df = load_df("""
        SELECT e.asn, AVG(f.suspiciousness) AS avg_suspiciousness
        FROM enriched_router_data e
        JOIN router_features f USING (router_hash)
        WHERE e.asn IS NOT NULL
        GROUP BY e.asn
        ORDER BY avg_suspiciousness DESC
        LIMIT 20
    """)

    plt.figure(figsize=(10, 6))
    plt.barh(df["asn"].astype(str)[::-1], df["avg_suspiciousness"][::-1], color="orange")
    plt.title("Top 20 ASNs by Avg Suspiciousness")
    plt.xlabel("Avg Suspiciousness")
    plt.ylabel("ASN")
    plt.tight_layout()
    plt.savefig(os.path.join(CHART_DIR, "asn_suspicious_distribution.png"))
    plt.close()


# ---------------------------------------------------------
# 5. Per-ASN detail charts (asn_detail page)
# ---------------------------------------------------------
def per_asn_detail_charts():
    """
    asn_detail.html embeds three per-ASN charts:
    asn_<asn>_{risk,suspicious,clusters}.png -- scoped to just that ASN's
    own routers, not the cross-ASN comparison charts above. asn here uses
    the DOUBLE-formatted string (e.g. "20473.0") since that's what
    enriched_router_data.asn actually is and what the detail route
    normalizes every incoming URL to.
    """
    asns = load_df("""
        SELECT DISTINCT asn FROM enriched_router_data WHERE asn IS NOT NULL
    """)["asn"].tolist()

    for asn in asns:
        con = duckdb.connect(DB_PATH, read_only=True)
        df = con.execute("""
            SELECT f.risk_score, f.suspiciousness, b.cluster_id
            FROM enriched_router_data e
            JOIN router_features f USING (router_hash)
            LEFT JOIN router_behavior b USING (router_hash)
            WHERE e.asn = ?
        """, [asn]).df()
        con.close()

        if df.empty:
            continue

        plt.figure(figsize=(7, 4))
        plt.hist(df["risk_score"].dropna(), bins=_safe_bins(df["risk_score"]), color="darkred", edgecolor="black")
        plt.title(f"Risk Score Distribution (ASN {asn})")
        plt.xlabel("Risk Score")
        plt.ylabel("Count")
        plt.tight_layout()
        plt.savefig(os.path.join(CHART_DIR, f"asn_{asn}_risk.png"))
        plt.close()

        plt.figure(figsize=(7, 4))
        plt.hist(df["suspiciousness"].dropna(), bins=_safe_bins(df["suspiciousness"]), color="orange", edgecolor="black")
        plt.title(f"Suspiciousness Distribution (ASN {asn})")
        plt.xlabel("Suspiciousness")
        plt.ylabel("Count")
        plt.tight_layout()
        plt.savefig(os.path.join(CHART_DIR, f"asn_{asn}_suspicious.png"))
        plt.close()

        cluster_counts = df["cluster_id"].value_counts().sort_index()
        plt.figure(figsize=(7, 4))
        plt.bar(cluster_counts.index.astype(str), cluster_counts.values, color="steelblue")
        plt.title(f"Cluster Distribution (ASN {asn})")
        plt.xlabel("Cluster ID")
        plt.ylabel("Router Count")
        plt.tight_layout()
        plt.savefig(os.path.join(CHART_DIR, f"asn_{asn}_clusters.png"))
        plt.close()


def run_asn_charts():
    print("[CHARTS] Building ASN router distribution…")
    asn_router_distribution()

    print("[CHARTS] Building ASN risk distribution…")
    asn_risk_distribution()

    print("[CHARTS] Building ASN anomaly distribution…")
    asn_anomaly_distribution()

    print("[CHARTS] Building ASN suspicious distribution…")
    asn_suspicious_distribution()

    print("[CHARTS] Building per-ASN detail charts…")
    per_asn_detail_charts()

    print("[CHARTS] ASN charts complete.")


if __name__ == "__main__":
    run_asn_charts()
