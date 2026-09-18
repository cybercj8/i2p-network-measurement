import os
import duckdb
import pandas as pd
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
# Country x version-outdatedness
# ---------------------------------------------------------
def country_version_risk_chart():
    df = load_df("""
        SELECT e.country_geo AS country, AVG(COALESCE(r.version_risk, 0)) AS avg_version_risk,
               COUNT(*) AS router_count
        FROM enriched_router_data e
        LEFT JOIN router_risk_scores r ON r.router_hash = e.router_hash
        WHERE e.country_geo IS NOT NULL
        GROUP BY e.country_geo
        HAVING COUNT(*) >= 5
        ORDER BY avg_version_risk DESC
        LIMIT 20
    """)

    plt.figure(figsize=(10, 6))
    plt.barh(df["country"][::-1], df["avg_version_risk"][::-1], color="firebrick", edgecolor="black")
    plt.title("Top 20 Countries by Avg Version Outdatedness (min 5 routers)")
    plt.xlabel("Avg Version Risk (0 = newest, 1 = oldest observed)")
    plt.ylabel("Country")
    plt.tight_layout()
    plt.savefig(os.path.join(CHART_DIR, "country_version_risk.png"))
    plt.close()


# ---------------------------------------------------------
# Country x floodfill capability adoption
# ---------------------------------------------------------
def country_floodfill_pct_chart():
    df = load_df("""
        SELECT
            country_geo AS country,
            100.0 * SUM(CASE WHEN is_floodfill_cap THEN 1 ELSE 0 END) / COUNT(*) AS pct_floodfill,
            COUNT(*) AS router_count
        FROM enriched_router_data
        WHERE country_geo IS NOT NULL
        GROUP BY country_geo
        HAVING COUNT(*) >= 5
        ORDER BY pct_floodfill DESC
        LIMIT 20
    """)

    plt.figure(figsize=(10, 6))
    plt.barh(df["country"][::-1], df["pct_floodfill"][::-1], color="steelblue", edgecolor="black")
    plt.title("Top 20 Countries by % Floodfill-Capable Routers (min 5 routers)")
    plt.xlabel("% Floodfill Capable")
    plt.ylabel("Country")
    plt.tight_layout()
    plt.savefig(os.path.join(CHART_DIR, "country_floodfill_pct.png"))
    plt.close()


# ---------------------------------------------------------
# Network-wide router type (Floodfill vs Regular Peer)
# ---------------------------------------------------------
# "Router type" in I2P's own architecture is primarily this distinction --
# floodfill routers participate in netDb DHT storage/distribution, regular
# peers don't. This is different from "implementation" (Java I2P/i2pd/I2P+),
# which is NOT reliably determinable from passive netDb data (see
# pipeline/known_implementations.py) -- router *type* is a real, correctly
# observable signal via the "f" capability flag.
def network_router_type_chart():
    df = load_df("""
        SELECT
            CASE WHEN is_floodfill_cap THEN 'Floodfill' ELSE 'Regular Peer' END AS router_type,
            COUNT(*) AS router_count
        FROM enriched_router_data
        GROUP BY router_type
    """)

    plt.figure(figsize=(6, 6))
    plt.pie(df["router_count"], labels=df["router_type"], autopct="%1.1f%%",
            colors=["#4682B4", "#B0C4DE"], startangle=90)
    plt.title("Network-Wide Router Type Distribution")
    plt.tight_layout()
    plt.savefig(os.path.join(CHART_DIR, "network_router_type.png"))
    plt.close()


# ---------------------------------------------------------
# Network-wide bandwidth class distribution
# ---------------------------------------------------------
# Bucketed by each router's HIGHEST advertised tier -- per the I2P spec a
# router may publish more than one bandwidth letter for backward
# compatibility (e.g. "PO"), so counting every flag independently would
# double-count routers; this takes the ceiling instead.
def network_bandwidth_class_chart():
    df = load_df("""
        SELECT
            CASE
                WHEN bw_unlimited THEN 'Unlimited (>2000 KBps)'
                WHEN bw_high THEN 'High (128-2000 KBps)'
                WHEN bw_mid THEN 'Mid (48-128 KBps)'
                WHEN bw_low THEN 'Low (<48 KBps)'
                ELSE 'Unknown'
            END AS bandwidth_class,
            COUNT(*) AS router_count
        FROM enriched_router_data
        GROUP BY bandwidth_class
    """)

    order = ["Low (<48 KBps)", "Mid (48-128 KBps)", "High (128-2000 KBps)", "Unlimited (>2000 KBps)", "Unknown"]
    df["bandwidth_class"] = pd.Categorical(df["bandwidth_class"], categories=order, ordered=True)
    df = df.sort_values("bandwidth_class")

    plt.figure(figsize=(9, 5))
    plt.bar(df["bandwidth_class"], df["router_count"], color="darkorange", edgecolor="black")
    plt.title("Network-Wide Bandwidth Class Distribution")
    plt.ylabel("Router Count")
    plt.xticks(rotation=20)
    plt.tight_layout()
    plt.savefig(os.path.join(CHART_DIR, "network_bandwidth_class.png"))
    plt.close()


# ---------------------------------------------------------
# Country x bandwidth class (avg tier, ordinal 0=low..3=unlimited)
# ---------------------------------------------------------
def country_bandwidth_class_chart():
    df = load_df("""
        SELECT
            country_geo AS country,
            AVG(CASE
                WHEN bw_unlimited THEN 3
                WHEN bw_high THEN 2
                WHEN bw_mid THEN 1
                WHEN bw_low THEN 0
                ELSE NULL
            END) AS avg_bw_tier,
            COUNT(*) AS router_count
        FROM enriched_router_data
        WHERE country_geo IS NOT NULL
        GROUP BY country_geo
        HAVING COUNT(*) >= 5
        ORDER BY avg_bw_tier DESC
        LIMIT 20
    """)

    plt.figure(figsize=(10, 6))
    plt.barh(df["country"][::-1], df["avg_bw_tier"][::-1], color="darkorange", edgecolor="black")
    plt.title("Top 20 Countries by Avg Bandwidth Tier (min 5 routers)")
    plt.xlabel("Avg Bandwidth Tier (0=Low .. 3=Unlimited)")
    plt.ylabel("Country")
    plt.tight_layout()
    plt.savefig(os.path.join(CHART_DIR, "country_bandwidth_class.png"))
    plt.close()


# ---------------------------------------------------------
# Country x IPv6 adoption
# ---------------------------------------------------------
def country_ipv6_pct_chart():
    df = load_df("""
        SELECT
            country_geo AS country,
            100.0 * SUM(CASE WHEN supports_ipv6 THEN 1 ELSE 0 END) / COUNT(*) AS pct_ipv6,
            COUNT(*) AS router_count
        FROM enriched_router_data
        WHERE country_geo IS NOT NULL
        GROUP BY country_geo
        HAVING COUNT(*) >= 5
        ORDER BY pct_ipv6 DESC
        LIMIT 20
    """)

    plt.figure(figsize=(10, 6))
    plt.barh(df["country"][::-1], df["pct_ipv6"][::-1], color="seagreen", edgecolor="black")
    plt.title("Top 20 Countries by % IPv6-Capable Routers (min 5 routers)")
    plt.xlabel("% IPv6 Capable")
    plt.ylabel("Country")
    plt.tight_layout()
    plt.savefig(os.path.join(CHART_DIR, "country_ipv6_pct.png"))
    plt.close()


# ---------------------------------------------------------
# Country x uptime
# ---------------------------------------------------------
def country_uptime_chart():
    df = load_df("""
        SELECT
            e.country_geo AS country,
            AVG(f.uptime_hours) AS avg_uptime_hours,
            COUNT(*) AS router_count
        FROM enriched_router_data e
        JOIN router_features f USING (router_hash)
        WHERE e.country_geo IS NOT NULL
        GROUP BY e.country_geo
        HAVING COUNT(*) >= 5
        ORDER BY avg_uptime_hours DESC
        LIMIT 20
    """)

    plt.figure(figsize=(10, 6))
    plt.barh(df["country"][::-1], df["avg_uptime_hours"][::-1], color="teal", edgecolor="black")
    plt.title("Top 20 Countries by Avg Router Uptime (min 5 routers)")
    plt.xlabel("Avg Uptime (hours)")
    plt.ylabel("Country")
    plt.tight_layout()
    plt.savefig(os.path.join(CHART_DIR, "country_uptime.png"))
    plt.close()


def run_geo_correlation_charts():
    print("[CHARTS] Building country version-risk chart…")
    country_version_risk_chart()

    print("[CHARTS] Building country floodfill% chart…")
    country_floodfill_pct_chart()

    print("[CHARTS] Building country IPv6% chart…")
    country_ipv6_pct_chart()

    print("[CHARTS] Building network-wide router type chart…")
    network_router_type_chart()

    print("[CHARTS] Building network-wide bandwidth class chart…")
    network_bandwidth_class_chart()

    print("[CHARTS] Building country bandwidth class chart…")
    country_bandwidth_class_chart()

    print("[CHARTS] Building country uptime chart…")
    country_uptime_chart()

    print("[CHARTS] Geo correlation charts complete.")


if __name__ == "__main__":
    run_geo_correlation_charts()
