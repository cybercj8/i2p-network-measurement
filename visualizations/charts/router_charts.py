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
    """Guard against matplotlib's "Too many bins for data range" crash when
    a metric's values are all identical or near-identical (near-zero
    range)."""
    data = data.dropna()
    if len(data) == 0:
        return 1
    if (data.max() - data.min()) <= 1e-9:
        return 1
    return min(max_bins, data.nunique())


# ---------------------------------------------------------
# 1. Entropy distribution
# ---------------------------------------------------------
def entropy_distribution():
    df = load_df("SELECT entropy FROM router_behavior WHERE entropy IS NOT NULL")
    plt.figure(figsize=(8, 5))
    plt.hist(df["entropy"], bins=_safe_bins(df["entropy"]), color="steelblue", edgecolor="black")
    plt.title("Router Entropy Distribution")
    plt.xlabel("Entropy")
    plt.ylabel("Count")
    plt.tight_layout()
    plt.savefig(os.path.join(CHART_DIR, "entropy_distribution.png"))
    plt.close()


# ---------------------------------------------------------
# 2. Periodicity distribution
# ---------------------------------------------------------
def periodicity_distribution():
    df = load_df("SELECT periodicity FROM router_behavior WHERE periodicity IS NOT NULL")
    plt.figure(figsize=(8, 5))
    plt.hist(df["periodicity"], bins=_safe_bins(df["periodicity"]), color="darkorange", edgecolor="black")
    plt.title("Router Periodicity Distribution")
    plt.xlabel("Periodicity")
    plt.ylabel("Count")
    plt.tight_layout()
    plt.savefig(os.path.join(CHART_DIR, "periodicity_distribution.png"))
    plt.close()


# ---------------------------------------------------------
# 3. Stability distribution
# ---------------------------------------------------------
def stability_distribution():
    df = load_df("SELECT stability FROM router_behavior WHERE stability IS NOT NULL")
    plt.figure(figsize=(8, 5))
    plt.hist(df["stability"], bins=_safe_bins(df["stability"]), color="seagreen", edgecolor="black")
    plt.title("Router Stability Distribution")
    plt.xlabel("Stability (observation count)")
    plt.ylabel("Count")
    plt.tight_layout()
    plt.savefig(os.path.join(CHART_DIR, "stability_distribution.png"))
    plt.close()


# ---------------------------------------------------------
# 4. Uptime slope distribution
# ---------------------------------------------------------
def uptime_slope_distribution():
    df = load_df("SELECT uptime_hours FROM router_features WHERE uptime_hours IS NOT NULL")
    plt.figure(figsize=(8, 5))
    plt.hist(df["uptime_hours"], bins=_safe_bins(df["uptime_hours"]), color="purple", edgecolor="black")
    plt.title("Router Uptime Distribution")
    plt.xlabel("Uptime (hours)")
    plt.ylabel("Count")
    plt.tight_layout()
    plt.savefig(os.path.join(CHART_DIR, "uptime_slope_distribution.png"))
    plt.close()


# ---------------------------------------------------------
# 5. IPv6 adoption
# ---------------------------------------------------------
def ipv6_adoption():
    df = load_df("""
        SELECT ipv6, COUNT(*) AS count
        FROM enriched_router_data
        GROUP BY ipv6
    """)

    # .astype(str) on a nullable-boolean column turns missing values into
    # a literal float('nan') rather than the string "nan" (a pandas
    # extension-array quirk), which matplotlib's bar() rejects outright --
    # map explicitly instead so every category is a real string.
    df["ipv6"] = df["ipv6"].map({True: "IPv6", False: "IPv4-only"}).fillna("Unknown")

    plt.figure(figsize=(8, 5))
    plt.bar(df["ipv6"], df["count"], color="purple")
    plt.title("IPv6 Adoption")
    plt.xlabel("IPv6 Enabled")
    plt.ylabel("Count")
    plt.tight_layout()
    plt.savefig(os.path.join(CHART_DIR, "ipv6_adoption.png"))
    plt.close()


def run_router_charts():
    print("[CHARTS] Building entropy distribution…")
    entropy_distribution()

    print("[CHARTS] Building periodicity distribution…")
    periodicity_distribution()

    print("[CHARTS] Building stability distribution…")
    stability_distribution()

    print("[CHARTS] Building uptime slope distribution…")
    uptime_slope_distribution()

    print("[CHARTS] Building IPv6 adoption chart…")
    ipv6_adoption()

    print("[CHARTS] Router charts complete.")


if __name__ == "__main__":
    run_router_charts()
