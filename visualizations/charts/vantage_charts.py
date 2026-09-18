"""
Vantage point comparison: how much do the Java I2P and i2pd router's views
of the network actually overlap?

Both vantage points independently discover routers via network gossip.
Comparing their observed sets is the actual empirical answer to "do
different implementations see the same slice of the network" -- not just
an assumption. The daily-trend chart specifically exists to check for
reseed-bootstrap bias: both routers may have initially pulled overlapping
peer lists from shared reseed servers, which could inflate apparent overlap
in the first day or two before organic gossip diverges their views. If
overlap % trends downward over the collection window, that's a real,
citable finding about how quickly the two implementations' views diverge --
not noise.
"""

import os
import duckdb
import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt

DB_PATH = "data/i2p.duckdb"
CHART_DIR = os.path.join(os.getcwd(), "visualizations", "charts")
os.makedirs(CHART_DIR, exist_ok=True)


def vantage_overlap_trend_chart():
    con = duckdb.connect(DB_PATH, read_only=True)
    rows = con.execute("""
        SELECT DISTINCT date_trunc('day', timestamp) AS day, seen_by, router_hash
        FROM router_snapshots
        WHERE seen_by IS NOT NULL
    """).fetchall()
    con.close()

    if not rows:
        print("[VANTAGE CHARTS] No seen_by data yet, skipping trend chart.")
        return

    by_day = {}
    for day, seen_by, router_hash in rows:
        by_day.setdefault(day, {"java_i2p": set(), "i2pd": set()})
        if seen_by in by_day[day]:
            by_day[day][seen_by].add(router_hash)

    days = sorted(by_day.keys())
    jaccard_pct = []
    for day in days:
        java_set = by_day[day]["java_i2p"]
        i2pd_set = by_day[day]["i2pd"]
        union = java_set | i2pd_set
        intersection = java_set & i2pd_set
        jaccard_pct.append(100.0 * len(intersection) / len(union) if union else 0.0)

    plt.figure(figsize=(10, 5))
    plt.plot(days, jaccard_pct, marker="o", color="purple")
    plt.title("Vantage Point Overlap (Jaccard %) Over Time")
    plt.xlabel("Day")
    plt.ylabel("Jaccard Similarity % (|both| / |either|)")
    plt.xticks(rotation=30)
    plt.ylim(0, 100)

    # With few (or just one) days of data, matplotlib's default date-axis
    # auto-scaling can pick an absurdly wide range (e.g. back to 2024) since
    # there's no real span to calculate from -- pin the x-axis explicitly
    # to the actual data range (+/- half a day of padding) instead.
    import datetime
    pad = datetime.timedelta(hours=12)
    plt.xlim(days[0] - pad, days[-1] + pad)

    plt.tight_layout()
    plt.savefig(os.path.join(CHART_DIR, "vantage_overlap_trend.png"))
    plt.close()


def run_vantage_charts():
    print("[VANTAGE CHARTS] Building vantage point overlap trend chart…")
    vantage_overlap_trend_chart()
    print("[VANTAGE CHARTS] Done.")


if __name__ == "__main__":
    run_vantage_charts()
