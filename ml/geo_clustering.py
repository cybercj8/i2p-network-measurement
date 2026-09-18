"""
Pure geographic clustering via DBSCAN on router coordinates.

This is deliberately separate from the existing behavioral clustering in
rebuild_behavior.py / enriched_behavior_engine.py, which runs KMeans over a
mixed feature vector (ASN, version, capabilities, lat/lon all together) --
that answers "which routers behave alike," not "which routers are
geographically co-located." This module answers the latter: it clusters
purely on (latitude, longitude) using the haversine (great-circle) metric,
so cluster shape reflects real-world distance rather than naive Euclidean
distance on degrees (which distorts badly away from the equator).

DBSCAN (rather than KMeans) is the right tool here because it doesn't
require picking a number of clusters up front -- it finds density-based
geographic hot-spots and correctly labels sparse/isolated routers as noise
(-1) rather than forcing them into a nearest cluster.
"""

import duckdb
import numpy as np
from sklearn.cluster import DBSCAN

DB_PATH = "data/i2p.duckdb"

EARTH_RADIUS_KM = 6371.0
CLUSTER_RADIUS_KM = 250.0  # routers within ~250km of each other's cluster core
MIN_SAMPLES = 5            # minimum routers to form a geographic cluster


def run_geo_clustering():
    con = duckdb.connect(DB_PATH)

    con.execute("""
        CREATE TABLE IF NOT EXISTS router_geo_clusters (
            router_hash TEXT PRIMARY KEY,
            geo_cluster_id INTEGER,
            latitude DOUBLE,
            longitude DOUBLE
        )
    """)

    rows = con.execute("""
        SELECT router_hash, latitude, longitude
        FROM enriched_router_data
        WHERE latitude IS NOT NULL AND longitude IS NOT NULL
    """).fetchall()

    if len(rows) < MIN_SAMPLES:
        print(f"[GEO CLUSTERING] Only {len(rows)} geolocated routers, need at least {MIN_SAMPLES}. Skipping.")
        con.close()
        return

    router_hashes = [r[0] for r in rows]
    coords_deg = np.array([[r[1], r[2]] for r in rows])
    coords_rad = np.radians(coords_deg)

    eps_rad = CLUSTER_RADIUS_KM / EARTH_RADIUS_KM

    print(f"[GEO CLUSTERING] Running DBSCAN on {len(rows)} routers "
          f"(eps={CLUSTER_RADIUS_KM}km, min_samples={MIN_SAMPLES})...")

    db = DBSCAN(eps=eps_rad, min_samples=MIN_SAMPLES, metric="haversine")
    labels = db.fit_predict(coords_rad)

    n_clusters = len(set(labels)) - (1 if -1 in labels else 0)
    n_noise = int(np.sum(labels == -1))
    print(f"[GEO CLUSTERING] Found {n_clusters} geographic clusters, "
          f"{n_noise} routers classified as noise/isolated ({n_noise/len(rows)*100:.1f}%)")

    con.execute("DELETE FROM router_geo_clusters")
    con.executemany(
        "INSERT INTO router_geo_clusters VALUES (?, ?, ?, ?)",
        [
            (router_hashes[i], int(labels[i]), float(coords_deg[i][0]), float(coords_deg[i][1]))
            for i in range(len(rows))
        ]
    )

    con.close()
    print("[GEO CLUSTERING] Done.")


if __name__ == "__main__":
    run_geo_clustering()
