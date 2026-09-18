import os
import duckdb
import pandas as pd
import matplotlib.pyplot as plt
from datetime import datetime

DB_PATH = "data/i2p.duckdb"

# Unified output directory
BASE_DIR = os.path.join(os.getcwd(), "visualizations", "charts")
os.makedirs(BASE_DIR, exist_ok=True)


def timestamp():
    return datetime.now().strftime("%Y%m%d_%H%M%S")


def save_chart(fig, name):
    fname = f"{name}_{timestamp()}.png"
    path = os.path.join(BASE_DIR, fname)
    fig.savefig(path, dpi=150, bbox_inches="tight")
    plt.close(fig)
    print(f"[VIS] Saved chart: {path}")


def run_visualizations():
    con = duckdb.connect(DB_PATH)

    # ---------------------------------------------------------
    # Example 1: ASN risk distribution
    # ---------------------------------------------------------
    df = con.execute("""
        SELECT asn, risk_score
        FROM asn_info
        WHERE risk_score IS NOT NULL
    """).df()

    if not df.empty:
        fig = plt.figure(figsize=(10, 6))
        plt.hist(df["risk_score"], bins=30, color="steelblue")
        plt.title("ASN Risk Score Distribution")
        plt.xlabel("Risk Score")
        plt.ylabel("Count")
        save_chart(fig, "asn_risk_distribution")

    # ---------------------------------------------------------
    # Example 2: Router count per ASN
    # ---------------------------------------------------------
    df2 = con.execute("""
        SELECT asn, router_count
        FROM asn_info
        WHERE router_count > 0
    """).df()

    if not df2.empty:
        fig = plt.figure(figsize=(12, 6))
        plt.bar(df2["asn"].astype(str), df2["router_count"], color="darkgreen")
        plt.title("Routers per ASN")
        plt.xlabel("ASN")
        plt.ylabel("Router Count")
        plt.xticks(rotation=90)
        save_chart(fig, "routers_per_asn")

    con.close()

