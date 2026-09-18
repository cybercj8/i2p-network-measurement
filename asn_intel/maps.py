import os
import duckdb
import pandas as pd
import matplotlib.pyplot as plt
from datetime import datetime

DB_PATH = "data/i2p.duckdb"

# Unified output directory
BASE_DIR = os.path.join(os.getcwd(), "visualizations", "maps")
os.makedirs(BASE_DIR, exist_ok=True)


def timestamp():
    return datetime.now().strftime("%Y%m%d_%H%M%S")


def save_map(fig, name):
    fname = f"{name}_{timestamp()}.png"
    path = os.path.join(BASE_DIR, fname)
    fig.savefig(path, dpi=150, bbox_inches="tight")
    plt.close(fig)
    print(f"[MAP] Saved map: {path}")


def run_maps():
    con = duckdb.connect(DB_PATH)

    df = con.execute("""
        SELECT latitude, longitude, asn
        FROM enriched_router_data
        WHERE latitude IS NOT NULL AND longitude IS NOT NULL
    """).df()

    if df.empty:
        print("[MAP] No geo data available.")
        return

    # ---------------------------------------------------------
    # Simple scatter map
    # ---------------------------------------------------------
    fig = plt.figure(figsize=(12, 6))
    plt.scatter(df["longitude"], df["latitude"], s=10, alpha=0.6)
    plt.title("Global Router Distribution")
    plt.xlabel("Longitude")
    plt.ylabel("Latitude")
    save_map(fig, "global_router_distribution")

    con.close()

