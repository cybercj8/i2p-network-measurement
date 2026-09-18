"""
Sensitivity analysis for the pure geographic DBSCAN clustering
(ml/geo_clustering.py).

The main clustering uses a single, somewhat arbitrary radius (250km) to
decide what counts as "geographically co-located." A finding like "72
geographic clusters" is only meaningful if it's not just an artifact of
that one radius choice -- this re-runs DBSCAN at a few different radii and
reports both how the cluster/noise counts move AND, more importantly, the
Adjusted Rand Index (ARI) against the baseline: whether the SAME routers
keep ending up grouped together, not just whether the totals happen to be
similar. ARI near 1.0 means the clustering is robust to the exact radius
chosen; a low ARI would mean the 250km figure is arbitrary and shouldn't be
reported as a stable finding.
"""

import duckdb
import numpy as np
from sklearn.cluster import DBSCAN
from sklearn.metrics import adjusted_rand_score

DB_PATH = "data/i2p.duckdb"
EARTH_RADIUS_KM = 6371.0
MIN_SAMPLES = 5
BASELINE_RADIUS_KM = 250.0
TEST_RADII_KM = [100.0, 250.0, 500.0]


def run_geo_clustering_sensitivity():
    con = duckdb.connect(DB_PATH)

    con.execute("""
        CREATE TABLE IF NOT EXISTS geo_clustering_sensitivity (
            radius_km DOUBLE,
            n_clusters INTEGER,
            n_noise INTEGER,
            noise_pct DOUBLE,
            ari_vs_baseline DOUBLE,
            router_count INTEGER
        )
    """)
    con.execute("DELETE FROM geo_clustering_sensitivity")

    rows = con.execute("""
        SELECT router_hash, latitude, longitude
        FROM enriched_router_data
        WHERE latitude IS NOT NULL AND longitude IS NOT NULL
    """).fetchall()

    if len(rows) < MIN_SAMPLES:
        print(f"[GEO SENSITIVITY] Only {len(rows)} geolocated routers, need at least {MIN_SAMPLES}. Skipping.")
        con.close()
        return

    coords_rad = np.radians(np.array([[r[1], r[2]] for r in rows]))

    labels_by_radius = {}
    for radius_km in TEST_RADII_KM:
        eps_rad = radius_km / EARTH_RADIUS_KM
        db = DBSCAN(eps=eps_rad, min_samples=MIN_SAMPLES, metric="haversine")
        labels = db.fit_predict(coords_rad)
        labels_by_radius[radius_km] = labels

    baseline_labels = labels_by_radius[BASELINE_RADIUS_KM]

    print(f"[GEO SENSITIVITY] Comparing DBSCAN results across radii {TEST_RADII_KM} "
          f"(baseline={BASELINE_RADIUS_KM}km) on {len(rows)} routers...")

    results = []
    for radius_km in TEST_RADII_KM:
        labels = labels_by_radius[radius_km]
        n_clusters = len(set(labels)) - (1 if -1 in labels else 0)
        n_noise = int(np.sum(labels == -1))
        noise_pct = 100.0 * n_noise / len(labels)
        ari = adjusted_rand_score(baseline_labels, labels)

        results.append((radius_km, n_clusters, n_noise, noise_pct, ari, len(rows)))
        print(f"[GEO SENSITIVITY] radius={radius_km}km: clusters={n_clusters}, "
              f"noise={n_noise} ({noise_pct:.1f}%), ARI vs {BASELINE_RADIUS_KM}km baseline={ari:.3f}")

    con.executemany(
        "INSERT INTO geo_clustering_sensitivity VALUES (?, ?, ?, ?, ?, ?)",
        results
    )

    con.close()
    print("[GEO SENSITIVITY] Done.")


if __name__ == "__main__":
    run_geo_clustering_sensitivity()
