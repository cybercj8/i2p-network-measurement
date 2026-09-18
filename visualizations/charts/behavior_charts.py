import os
import duckdb
import pandas as pd
import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt

DB_PATH = "data/i2p.duckdb"
CHART_DIR = os.path.join(os.getcwd(), "visualizations", "charts")
os.makedirs(CHART_DIR, exist_ok=True)


def load_df(query, params=None):
    con = duckdb.connect(DB_PATH, read_only=True)
    df = con.execute(query, params) if params else con.execute(query)
    df = df.df()
    con.close()
    return df


# ---------------------------------------------------------
# Per-router behavior trend charts (behavior_detail page)
# ---------------------------------------------------------
def generate_router_behavior_charts(router_hash):
    """
    Produces the 5 per-router trend charts the behavior detail page embeds:
    behavior_<hash>_{entropy,periodicity,stability,uptime,cluster_distance}.png

    entropy/periodicity/stability come from router_behavior_history, which
    accumulates one row per router per pipeline run (a real time series).
    uptime comes from timeseries_router_stats for the same reason.
    cluster_distance has no historical table behind it (router_behavior only
    ever holds the current value), so it's shown as a single current-value
    bar rather than a fabricated trend.
    """
    hist = load_df("""
        SELECT timestamp, entropy, periodicity, stability
        FROM router_behavior_history
        WHERE router_hash = ?
        ORDER BY timestamp
    """, [router_hash])

    if not hist.empty:
        hist["ts"] = pd.to_datetime(hist["timestamp"], unit="ms")

    def trend_chart(column, color, title, filename):
        plt.figure(figsize=(8, 4))
        if hist.empty or hist[column].dropna().empty:
            plt.text(0.5, 0.5, "No history yet", ha="center", va="center")
        else:
            plt.plot(hist["ts"], hist[column], marker="o", color=color)
            plt.xticks(rotation=30)
        plt.title(title)
        plt.xlabel("Time")
        plt.ylabel(column.capitalize())
        plt.tight_layout()
        plt.savefig(os.path.join(CHART_DIR, filename))
        plt.close()

    trend_chart("entropy", "steelblue", f"Entropy Over Time", f"behavior_{router_hash}_entropy.png")
    trend_chart("periodicity", "darkorange", f"Periodicity Over Time", f"behavior_{router_hash}_periodicity.png")
    trend_chart("stability", "seagreen", f"Stability Over Time", f"behavior_{router_hash}_stability.png")

    uptime_hist = load_df("""
        SELECT timestamp, uptime_hours
        FROM timeseries_router_stats
        WHERE router_hash = ? AND uptime_hours IS NOT NULL
        ORDER BY timestamp
    """, [router_hash])

    plt.figure(figsize=(8, 4))
    if uptime_hist.empty:
        plt.text(0.5, 0.5, "No history yet", ha="center", va="center")
    else:
        plt.plot(uptime_hist["timestamp"], uptime_hist["uptime_hours"], marker="o", color="purple")
        plt.xticks(rotation=30)
    plt.title("Uptime Over Time")
    plt.xlabel("Time")
    plt.ylabel("Uptime (hours)")
    plt.tight_layout()
    plt.savefig(os.path.join(CHART_DIR, f"behavior_{router_hash}_uptime.png"))
    plt.close()

    current = load_df("""
        SELECT cluster_distance FROM router_behavior WHERE router_hash = ?
    """, [router_hash])

    plt.figure(figsize=(4, 4))
    value = current["cluster_distance"].iloc[0] if not current.empty else None
    if value is None:
        plt.text(0.5, 0.5, "No data", ha="center", va="center")
    else:
        plt.bar(["Current"], [value], color="darkred")
    plt.title("Current Cluster Distance")
    plt.ylabel("Distance from Cluster Centroid")
    plt.tight_layout()
    plt.savefig(os.path.join(CHART_DIR, f"behavior_{router_hash}_cluster_distance.png"))
    plt.close()


# ---------------------------------------------------------
# 1. Entropy vs periodicity (behavioral shape)
# ---------------------------------------------------------
def entropy_periodicity_overlay():
    df = load_df("""
        SELECT entropy, periodicity
        FROM router_behavior
        WHERE entropy IS NOT NULL AND periodicity IS NOT NULL
    """)

    plt.figure(figsize=(8, 6))
    plt.scatter(df["entropy"], df["periodicity"], alpha=0.3, s=10, color="teal")
    plt.title("Router Entropy vs Periodicity")
    plt.xlabel("Entropy")
    plt.ylabel("Periodicity")
    plt.tight_layout()
    plt.savefig(os.path.join(CHART_DIR, "entropy_periodicity_overlay.png"))
    plt.close()


# ---------------------------------------------------------
# 2. Churn event type distribution
# ---------------------------------------------------------
def churn_event_distribution():
    try:
        df = load_df("""
            SELECT event, COUNT(*) AS count
            FROM router_churn
            WHERE event IS NOT NULL
            GROUP BY event
            ORDER BY count DESC
        """)
    except Exception:
        print("[CHARTS] router_churn not available yet, skipping churn_event_distribution")
        return

    if df.empty:
        print("[CHARTS] No churn events yet, skipping churn_event_distribution")
        return

    plt.figure(figsize=(9, 5))
    plt.bar(df["event"], df["count"], color="slateblue")
    plt.title("Router Churn Events by Type")
    plt.xlabel("Event Type")
    plt.ylabel("Count")
    plt.xticks(rotation=20)
    plt.tight_layout()
    plt.savefig(os.path.join(CHART_DIR, "churn_event_distribution.png"))
    plt.close()


def run_behavior_charts():
    print("[CHARTS] Building entropy/periodicity overlay…")
    entropy_periodicity_overlay()

    print("[CHARTS] Building churn event distribution…")
    churn_event_distribution()

    print("[CHARTS] Behavior charts complete.")


if __name__ == "__main__":
    run_behavior_charts()
