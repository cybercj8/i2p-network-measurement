import os
import duckdb
import folium
from folium.plugins import MarkerCluster, HeatMap, TimestampedGeoJson

DB_PATH = "data/i2p.duckdb"
MAP_DIR = os.path.join(os.getcwd(), "visualizations", "maps")
os.makedirs(MAP_DIR, exist_ok=True)

WORLD_COUNTRIES_GEOJSON = os.path.join(os.path.dirname(__file__), "data", "world-countries.json")

# pycountry's official ISO short names don't always match the simplified
# common names used in the world-countries GeoJSON (e.g. "Russian
# Federation" vs "Russia") -- without this override, those countries would
# silently fail to join and render as unfilled/missing on the choropleth.
COUNTRY_NAME_OVERRIDES = {
    "US": "United States of America",
    "RU": "Russia",
    "KR": "South Korea",
    "KP": "North Korea",
    "IR": "Iran",
    "VE": "Venezuela",
    "VN": "Vietnam",
    "BO": "Bolivia",
    "TZ": "United Republic of Tanzania",
    "CD": "Democratic Republic of the Congo",
    "CG": "Republic of the Congo",
    "MD": "Moldova",
    "LA": "Laos",
    "SY": "Syria",
    "GB": "United Kingdom",
    "CZ": "Czech Republic",
    "TW": "Taiwan",
    "BN": "Brunei",
}


def iso_to_geojson_country_name(iso_code):
    if iso_code in COUNTRY_NAME_OVERRIDES:
        return COUNTRY_NAME_OVERRIDES[iso_code]
    try:
        import pycountry
        c = pycountry.countries.get(alpha_2=iso_code)
        return c.name if c else None
    except Exception:
        return None

RESOLVED_FEATURES_SQL = """
    SELECT
        e.router_hash,
        e.latitude,
        e.longitude,
        e.country_geo,
        e.asn,
        e.as_org,
        e.caps_raw,
        e.version_raw,
        b.cluster_id,
        b.cluster_distance,
        f.risk_score,
        f.anomaly_score,
        f.suspiciousness
    FROM enriched_router_data e
    LEFT JOIN router_behavior b USING (router_hash)
    LEFT JOIN router_features f USING (router_hash)
"""

RESOLVED_FEATURES_COLUMNS = [
    "router_hash", "latitude", "longitude", "country_geo", "asn", "as_org",
    "caps_raw", "version_raw", "cluster_id", "cluster_distance",
    "risk_score", "anomaly_score", "suspiciousness"
]


def get_conn():
    return duckdb.connect(DB_PATH, read_only=True)


def row_to_dict(row):
    return dict(zip(RESOLVED_FEATURES_COLUMNS, row))


def save_map(m, filename):
    path = os.path.join(MAP_DIR, filename)
    m.save(path)
    print(f"[MAP] {filename}")


def hex_for_cluster(cluster_id):
    if cluster_id is None:
        return "#888888"
    return f"#{(int(cluster_id) * 123457) % 0xFFFFFF:06x}"


def color_for_score(score, low_color="#2ca02c", mid_color="#ff7f0e", high_color="#d62728"):
    """0-1 style score -> green/orange/red gradient for risk/suspiciousness/cluster
    distance maps. Do NOT use this for anomaly_score -- see color_for_anomaly()."""
    if score is None:
        return "#888888"
    if score < 0.33:
        return low_color
    if score < 0.66:
        return mid_color
    return high_color


def color_for_anomaly(score, low_color="#2ca02c", mid_color="#ff7f0e", high_color="#d62728"):
    """
    anomaly_score follows IsolationForest's convention: 0 = normal, increasingly
    NEGATIVE = increasingly anomalous. This is nothing like risk_score/suspiciousness's
    0-1 range -- feeding anomaly_score through color_for_score() made every single
    router green (color_for_score's low/green threshold is < 0.33, and anomaly_score
    in this project never exceeds 0.0), regardless of how anomalous a router actually
    was. Thresholds below are calibrated to this project's real observed distribution
    (95th percentile ~= 0.0, 99th percentile ~= -0.04, min ~= -0.11), not guessed.
    """
    if score is None:
        return "#888888"
    if score >= -0.001:
        return low_color
    if score >= -0.03:
        return mid_color
    return high_color


# Shared legend content, reused across every map colored by these schemes.
LEGEND_SCORE = [("#2ca02c", "Low (< 0.33)"), ("#ff7f0e", "Moderate (0.33-0.66)"), ("#d62728", "High (>= 0.66)")]
LEGEND_ANOMALY = [("#2ca02c", "Normal"), ("#ff7f0e", "Somewhat anomalous"), ("#d62728", "Strongly anomalous")]
LEGEND_CLUSTER_ID = [(None, "Each color = one behavioral cluster (see /clusters for details)")]
LEGEND_GEO_CLUSTER = [(None, "Each color = one geographic cluster"), ("#888888", "Isolated / noise")]


def _base_map():
    return folium.Map(location=[20, 0], zoom_start=2, tiles="OpenStreetMap")


def _add_points(m, rows, color_fn, popup_fn, cluster=True):
    target = MarkerCluster().add_to(m) if cluster else m
    for r in rows:
        if r.get("latitude") is None or r.get("longitude") is None:
            continue
        folium.CircleMarker(
            location=[r["latitude"], r["longitude"]],
            radius=4,
            color=color_fn(r),
            fill=True,
            fill_opacity=0.7,
            popup=popup_fn(r),
        ).add_to(target)
    return m


def _heatmap(points, radius=15, blur=20):
    """
    Genuine density heatmap (folium.plugins.HeatMap), not the discrete
    colored-marker style _add_points() produces. points is a list of
    [lat, lon] or [lat, lon, weight] -- weight, if given, should be
    non-negative (HeatMap doesn't handle negative weights meaningfully).
    """
    m = _base_map()
    if points:
        HeatMap(points, radius=radius, blur=blur).add_to(m)
    return m


def _add_legend(m, title, items):
    """
    Injects a small fixed-position HTML legend into a folium map -- folium
    has no built-in legend widget for plain CircleMarker/Marker layers
    (only Choropleth auto-generates one). items is a list of (color, label)
    tuples; color can be any CSS color string, or None for a text-only line
    with no swatch (use this for hash-based categorical coloring like
    cluster_id/ASN/geo_cluster, where colors are arbitrary per-ID and no
    single swatch could correctly represent "this color means X" -- showing
    one anyway, e.g. a gray dot that never actually appears on the map,
    is misleading rather than merely incomplete).
    """
    rows = "".join(
        (
            f'<div style="display:flex;align-items:center;margin:2px 0;">'
            f'<span style="display:inline-block;width:12px;height:12px;border-radius:50%;'
            f'background:{color};margin-right:6px;flex-shrink:0;"></span>'
            f'<span>{label}</span></div>'
            if color is not None else
            f'<div style="margin:2px 0;">{label}</div>'
        )
        for color, label in items
    )
    legend_html = f"""
    <div style="position: fixed; bottom: 20px; left: 20px; z-index: 9999;
                background: white; padding: 10px 12px; border-radius: 6px;
                border: 1px solid #999; box-shadow: 0 1px 4px rgba(0,0,0,0.3);
                font-size: 12px; color: #222; font-family: sans-serif; max-width: 220px;">
        <div style="font-weight: bold; margin-bottom: 4px;">{title}</div>
        {rows}
    </div>
    """
    m.get_root().html.add_child(folium.Element(legend_html))


def _build_movement_map(movement_items, animate=True, light=False):
    """
    movement_items: dict of router_hash -> [(lat, lon, timestamp), ...]
    (already sorted by timestamp).

    Draws each router's per-router-colored trail, plus a green START marker
    and red END marker per router so direction is visible even on the
    static (unplayed) map, plus (if animate) a TimestampedGeoJson layer so
    trails draw themselves out chronologically on playback.

    light=True trades the richer per-router visual treatment for raw
    rendering speed, for use when movement_items has hundreds+ entries
    (the pin-icon markers and animation layer used for the curated
    "top movers" view get noticeably laggy at that scale in a real
    browser): plain small CircleMarkers instead of Icon pins, wrapped in a
    MarkerCluster (viewport virtualization -- only what's on screen
    actually renders), and no TimestampedGeoJson animation layer, which is
    the single heaviest part of this map by far at high router counts.
    """
    m = _base_map()
    features = []
    starts = []
    ends = []
    animate = animate and not light

    for router_hash, points in movement_items.items():
        if len(points) < 2:
            continue

        color = hex_for_cluster(hash(router_hash) % 1000)
        coords = [(lat, lon) for lat, lon, _ in points]

        folium.PolyLine(
            locations=coords,
            color=color,
            weight=2,
            opacity=0.6,
            popup=f"Router: {router_hash}",
        ).add_to(m)

        start_lat, start_lon, start_ts = points[0]
        end_lat, end_lon, end_ts = points[-1]
        starts.append((router_hash, start_lat, start_lon, start_ts))
        ends.append((router_hash, end_lat, end_lon, end_ts))

        if animate:
            times = [ts.isoformat() if hasattr(ts, "isoformat") else str(ts) for _, _, ts in points]
            geojson_coords = [[lon, lat] for lat, lon, _ in points]  # GeoJSON is [lon, lat]
            features.append({
                "type": "Feature",
                "geometry": {"type": "LineString", "coordinates": geojson_coords},
                "properties": {
                    "times": times,
                    "style": {"color": color, "weight": 3},
                    "icon": "circle",
                    "iconstyle": {"fillColor": color, "fillOpacity": 0.9, "radius": 6, "color": color},
                },
            })

    # Two SEPARATE passes (all starts, then all ends) rather than
    # interleaved per-router -- interleaving meant a later router's red
    # marker would frequently paint directly over an earlier router's green
    # marker wherever they were geographically close, making starts far
    # less visible than ends overall (confirmed visually).
    if light:
        start_group = MarkerCluster(name="starts").add_to(m)
        end_group = MarkerCluster(name="ends").add_to(m)
        for router_hash, lat, lon, ts in starts:
            folium.CircleMarker(
                location=[lat, lon], radius=5, color="green", fill=True,
                fill_color="green", fill_opacity=0.9,
                popup=f"START<br>Router: {router_hash}<br>{ts}",
            ).add_to(start_group)
        for router_hash, lat, lon, ts in ends:
            folium.CircleMarker(
                location=[lat, lon], radius=5, color="red", fill=True,
                fill_color="red", fill_opacity=0.9,
                popup=f"END (most recent)<br>Router: {router_hash}<br>{ts}",
            ).add_to(end_group)
    else:
        # Distinct pin-shaped icons (not the same circle style used for
        # risk/anomaly/etc elsewhere) so start/end read as a different kind
        # of marker entirely, not just "a green dot" vs "a red dot" that
        # can be mistaken for a differently-colored circle marker from
        # another layer. Only used for small (curated) router counts --
        # icon markers are meaningfully heavier to render than circles.
        for router_hash, lat, lon, ts in starts:
            folium.Marker(
                location=[lat, lon],
                icon=folium.Icon(color="green", icon="play", prefix="fa"),
                popup=f"START<br>Router: {router_hash}<br>{ts}",
                z_index_offset=1000,
            ).add_to(m)
        for router_hash, lat, lon, ts in ends:
            folium.Marker(
                location=[lat, lon],
                icon=folium.Icon(color="red", icon="stop", prefix="fa"),
                popup=f"END (most recent)<br>Router: {router_hash}<br>{ts}",
                z_index_offset=2000,
            ).add_to(m)

    if animate and features:
        TimestampedGeoJson(
            {"type": "FeatureCollection", "features": features},
            period="PT30M",
            duration="PT30M",
            add_last_point=True,
            auto_play=False,
            loop=False,
            transition_time=300,
        ).add_to(m)

    _add_legend(m, "Router Movement", [
        ("green", "Start (earliest observation)"),
        ("red", "End (most recent observation)"),
        ("#3388ff", "Path (color = router identity)"),
    ])

    return m


# --------------------------------------------
#   MAIN ORCHESTRATOR
# --------------------------------------------
# NOTE: name must stay `run_all_maps` -- pipeline/run_all.py imports it as-is.

def run_all_maps(limit=None):
    print("=== I2P Map Generator: starting ===")
    conn = get_conn()

    all_rows = [
        row_to_dict(r) for r in conn.execute(
            RESOLVED_FEATURES_SQL + " WHERE e.latitude IS NOT NULL AND e.longitude IS NOT NULL"
        ).fetchall()
    ]
    router_dict = {r["router_hash"]: r for r in all_rows}
    print(f"[INFO] {len(all_rows)} geolocated routers loaded")

    movement_rows = conn.execute("""
        SELECT router_hash, latitude, longitude, timestamp
        FROM router_location_history
        WHERE latitude IS NOT NULL AND longitude IS NOT NULL
        ORDER BY router_hash, timestamp
    """).fetchall()
    movement_dict = {}
    for router_hash, lat, lon, ts in movement_rows:
        movement_dict.setdefault(router_hash, []).append((lat, lon, ts))
    print(f"[INFO] movement history loaded for {len(movement_dict)} routers")

    try:
        geo_cluster_rows = conn.execute("""
            SELECT router_hash, geo_cluster_id, latitude, longitude
            FROM router_geo_clusters
        """).fetchall()
    except Exception:
        geo_cluster_rows = []  # table doesn't exist yet on a fresh database

    conn.close()

    # Must come after conn.close() -- this opens its own write connection,
    # and DuckDB refuses a second connection to the same file with a
    # different config (read_only vs not) while the read-only one is open.
    _write_movement_stats(movement_dict)

    _generate_global_maps(all_rows)
    _generate_filtered_maps(all_rows)
    _generate_behavior_maps(all_rows, movement_dict)
    _generate_country_maps(all_rows)
    _generate_asn_maps(all_rows)
    _generate_cluster_maps(all_rows)
    _generate_geo_cluster_map(geo_cluster_rows)
    _generate_router_maps(router_dict, movement_dict, limit=limit)
    _generate_top_movers_map(movement_dict)
    _generate_dashboard_aliases()

    print("=== I2P Map Generator: done ===")


# --------------------------------------------
#   GLOBAL MAPS
# --------------------------------------------

def _generate_global_maps(rows):
    print("--- Global maps ---")

    m = _base_map()
    _add_points(m, rows, lambda r: hex_for_cluster(r["cluster_id"]),
                lambda r: f"Router: {r['router_hash']}<br>Cluster: {r['cluster_id']}",
                cluster=False)
    _add_legend(m, "Behavioral Cluster", LEGEND_CLUSTER_ID)
    save_map(m, "global_map.html")

    # Genuine density heatmaps (folium HeatMap), weighted by the named
    # score -- distinct from the discrete colored-marker maps above.
    risk_points = [[r["latitude"], r["longitude"], max(r["risk_score"] or 0, 0)]
                   for r in rows if r.get("latitude") is not None]
    save_map(_heatmap(risk_points), "router_risk_heatmap.html")

    susp_points = [[r["latitude"], r["longitude"], max(r["suspiciousness"] or 0, 0)]
                   for r in rows if r.get("latitude") is not None]
    save_map(_heatmap(susp_points), "suspicious_heatmap.html")

    m = _base_map()
    _add_points(m, rows, lambda r: color_for_anomaly(r["anomaly_score"]),
                lambda r: f"Router: {r['router_hash']}<br>Anomaly: {r['anomaly_score']}",
                cluster=False)
    _add_legend(m, "Anomaly Score", LEGEND_ANOMALY)
    save_map(m, "anomaly_map.html")


# --------------------------------------------
#   FILTERED / ALIAS MAPS
# --------------------------------------------
# These back the *_filtered_map.html and misc alias views the dashboard
# links to before any query-string filters are applied -- unfiltered by
# default, same underlying data as the global maps.

def _generate_filtered_maps(rows):
    print("--- Filtered maps ---")

    m = _base_map()
    _add_points(m, rows, lambda r: hex_for_cluster(r["cluster_id"]),
                lambda r: f"Router: {r['router_hash']}",
                cluster=False)
    _add_legend(m, "Behavioral Cluster", LEGEND_CLUSTER_ID)
    save_map(m, "filtered_router_map.html")
    save_map(m, "router_filtered_map.html")

    m = _base_map()
    _add_points(m, rows, lambda r: color_for_score(r["suspiciousness"]),
                lambda r: f"Router: {r['router_hash']}<br>Suspiciousness: {r['suspiciousness']}",
                cluster=False)
    _add_legend(m, "Suspiciousness", LEGEND_SCORE)
    save_map(m, "suspicious_filtered_map.html")

    # Distinct from filtered_router_map.html/router_filtered_map.html above
    # (which already shows cluster_id as color) -- this shows cluster_distance,
    # how atypical each router is relative to its own cluster's centroid.
    m = _base_map()
    _add_points(m, rows, lambda r: color_for_score(r["cluster_distance"]),
                lambda r: f"Router: {r['router_hash']}<br>Cluster: {r['cluster_id']}<br>Cluster distance: {r['cluster_distance']}",
                cluster=False)
    _add_legend(m, "Cluster Distance", LEGEND_SCORE)
    save_map(m, "cluster_filtered_map.html")

    # Country-level aggregate (was previously coloring by each router's own
    # individual risk_score with a popup that didn't even mention it --
    # neither matched the "country" framing). Mirrors colored_country_map()
    # in _generate_country_maps().
    country_risk_avg = {}
    by_country_local = {}
    for r in rows:
        if r["country_geo"]:
            by_country_local.setdefault(r["country_geo"], []).append(r)
    for country, rs in by_country_local.items():
        country_risk_avg[country] = sum((r["risk_score"] or 0) for r in rs) / len(rs)

    m = _base_map()
    for r in rows:
        country = r["country_geo"]
        if not country or r["latitude"] is None:
            continue
        folium.CircleMarker(
            location=[r["latitude"], r["longitude"]],
            radius=4,
            color=color_for_score(country_risk_avg.get(country, 0)),
            fill=True,
            fill_opacity=0.7,
            popup=f"Country: {country}<br>Avg risk: {country_risk_avg.get(country, 0):.3f}",
        ).add_to(m)
    _add_legend(m, "Avg Risk by Country", LEGEND_SCORE)
    save_map(m, "country_filtered_map.html")


# --------------------------------------------
#   BEHAVIOR / MOVEMENT MAPS
# --------------------------------------------

def _generate_behavior_maps(rows, movement_dict):
    print("--- Behavior maps ---")

    m = _base_map()
    _add_points(m, rows, lambda r: hex_for_cluster(r["cluster_id"]),
                lambda r: f"Router: {r['router_hash']}<br>Cluster distance: {r['cluster_distance']}",
                cluster=False)
    _add_legend(m, "Behavioral Cluster", LEGEND_CLUSTER_ID)
    save_map(m, "behavior_outliers.html")
    save_map(m, "behavior_filtered_map.html")

    # Capped to the top 500 routers by distance traveled, not literally
    # every router with any movement at all (which can be 10,000+ routers
    # -- unusably laggy in a real browser: thousands of polylines plus
    # thousands of markers). 500 is still a lot more than the curated
    # "top 20" view and, per the ranking table on /movement, captures
    # everything with a meaningfully large amount of movement; the very
    # long tail of routers with only a small amount of movement contributes
    # little insight per marker at this map's zoomed-out scale anyway.
    FULL_MOVEMENT_MAP_CAP = 500
    ranked_by_distance = sorted(
        (
            (h, sum(
                _haversine_km(pts[i][0], pts[i][1], pts[i + 1][0], pts[i + 1][1])
                for i in range(len(pts) - 1)
            ))
            for h, pts in movement_dict.items() if len(pts) >= 2
        ),
        key=lambda x: x[1],
        reverse=True
    )[:FULL_MOVEMENT_MAP_CAP]
    capped_movement = {h: movement_dict[h] for h, _ in ranked_by_distance}

    save_map(_build_movement_map(capped_movement, light=True), "router_movement_map.html")


# --------------------------------------------
#   MOVEMENT STATS / TOP MOVERS
# --------------------------------------------

def _haversine_km(lat1, lon1, lat2, lon2):
    import math
    r = 6371.0
    p1, p2 = math.radians(lat1), math.radians(lat2)
    dp = math.radians(lat2 - lat1)
    dl = math.radians(lon2 - lon1)
    a = math.sin(dp / 2) ** 2 + math.cos(p1) * math.cos(p2) * math.sin(dl / 2) ** 2
    return 2 * r * math.asin(math.sqrt(a))


def _write_movement_stats(movement_dict):
    """
    Persists a router_movement_stats table (total distance traveled, number
    of distinct locations, observation count) so the /movement dashboard
    page can query a ranking directly instead of recomputing haversine
    distances over the full history on every page load.
    """
    con = duckdb.connect(DB_PATH)
    con.execute("""
        CREATE TABLE IF NOT EXISTS router_movement_stats (
            router_hash TEXT PRIMARY KEY,
            total_km DOUBLE,
            distinct_locations INTEGER,
            observations INTEGER,
            first_seen TIMESTAMP,
            last_seen TIMESTAMP
        )
    """)
    con.execute("DELETE FROM router_movement_stats")

    rows = []
    for router_hash, points in movement_dict.items():
        if len(points) < 2:
            continue
        total_km = sum(
            _haversine_km(points[i][0], points[i][1], points[i + 1][0], points[i + 1][1])
            for i in range(len(points) - 1)
        )
        distinct_locations = len({(round(lat, 3), round(lon, 3)) for lat, lon, _ in points})
        rows.append((
            router_hash, total_km, distinct_locations, len(points),
            points[0][2], points[-1][2]
        ))

    if rows:
        con.executemany(
            "INSERT INTO router_movement_stats VALUES (?, ?, ?, ?, ?, ?)", rows
        )
    con.close()
    print(f"[MOVEMENT STATS] {len(rows)} routers with movement recorded")


def _generate_top_movers_map(movement_dict, top_n=20):
    """
    Focused animated map of just the top N routers by total distance
    traveled -- the global router_movement_map.html includes every router
    with any movement at all, which gets visually cluttered; this is the
    curated "most interesting" view for the /movement page.
    """
    print("--- Top movers map ---")

    ranked = sorted(
        (
            (h, sum(
                _haversine_km(pts[i][0], pts[i][1], pts[i + 1][0], pts[i + 1][1])
                for i in range(len(pts) - 1)
            ))
            for h, pts in movement_dict.items() if len(pts) >= 2
        ),
        key=lambda x: x[1],
        reverse=True
    )[:top_n]

    top_hashes = {h for h, _ in ranked}
    top_movement = {h: movement_dict[h] for h in top_hashes}

    save_map(_build_movement_map(top_movement), "top_movers_map.html")


# --------------------------------------------
#   COUNTRY MAPS
# --------------------------------------------

def _generate_country_maps(rows):
    print("--- Country maps ---")

    by_country = {}
    for r in rows:
        if not r["country_geo"]:
            continue
        by_country.setdefault(r["country_geo"], []).append(r)

    def country_avg(field):
        return {
            country: sum((r[field] or 0) for r in rs) / len(rs)
            for country, rs in by_country.items()
        }

    risk_avg = country_avg("risk_score")
    anom_avg = country_avg("anomaly_score")
    susp_avg = country_avg("suspiciousness")

    def colored_country_map(avg_map, filename, color_fn=color_for_score, legend_title="Avg Score", legend=LEGEND_SCORE):
        m = _base_map()
        for r in rows:
            country = r["country_geo"]
            if not country or r["latitude"] is None:
                continue
            folium.CircleMarker(
                location=[r["latitude"], r["longitude"]],
                radius=4,
                color=color_fn(avg_map.get(country, 0)),
                fill=True,
                fill_opacity=0.7,
                popup=f"Country: {country}<br>Avg: {avg_map.get(country, 0):.3f}",
            ).add_to(m)
        _add_legend(m, legend_title, legend)
        save_map(m, filename)

    colored_country_map(risk_avg, "country_risk_map.html", legend_title="Avg Risk")
    colored_country_map(anom_avg, "country_anomaly_map.html", color_fn=color_for_anomaly,
                         legend_title="Avg Anomaly", legend=LEGEND_ANOMALY)
    colored_country_map(susp_avg, "country_behavior_map.html", legend_title="Avg Suspiciousness")

    # Choropleth: filled country borders shaded by router count -- distinct
    # from asn_density_map.html's individual point-cluster markers (ASNs
    # don't have natural polygon boundaries to fill; countries do).
    _generate_country_choropleth(by_country)

    # Genuine density heatmap -- geographic concentration of routers,
    # unweighted (every point contributes equally).
    density_points = [[r["latitude"], r["longitude"]]
                       for r in rows if r.get("latitude") is not None]
    save_map(_heatmap(density_points), "country_heatmap.html")


def _generate_country_choropleth(by_country):
    if not os.path.exists(WORLD_COUNTRIES_GEOJSON):
        print("[WARN] world-countries.json not found, skipping country choropleth.")
        return

    counts_by_geojson_name = {}
    unmatched = []
    for iso_code, rs in by_country.items():
        name = iso_to_geojson_country_name(iso_code)
        if name is None:
            unmatched.append(iso_code)
            continue
        counts_by_geojson_name[name] = len(rs)

    if unmatched:
        print(f"[WARN] {len(unmatched)} country codes had no GeoJSON name match, "
              f"excluded from choropleth: {unmatched}")

    m = _base_map()
    folium.Choropleth(
        geo_data=WORLD_COUNTRIES_GEOJSON,
        data=counts_by_geojson_name,
        key_on="feature.properties.name",
        fill_color="YlOrRd",
        fill_opacity=0.75,
        line_opacity=0.3,
        nan_fill_color="#d9d9d9",
        legend_name="Routers per Country",
    ).add_to(m)

    # Choropleth fill alone has no hover/click info -- add a thin invisible
    # tooltip layer so a country's exact count is still discoverable.
    import json as _json
    with open(WORLD_COUNTRIES_GEOJSON) as f:
        geojson_data = _json.load(f)
    for feature in geojson_data["features"]:
        feature["properties"]["router_count"] = counts_by_geojson_name.get(
            feature["properties"]["name"], 0
        )
    folium.GeoJson(
        geojson_data,
        style_function=lambda x: {"fillOpacity": 0, "weight": 0},
        tooltip=folium.GeoJsonTooltip(fields=["name", "router_count"],
                                       aliases=["Country:", "Routers:"]),
    ).add_to(m)

    save_map(m, "country_density_map.html")


# --------------------------------------------
#   ASN MAPS
# --------------------------------------------

def _generate_asn_maps(rows):
    print("--- ASN maps ---")

    by_asn = {}
    for r in rows:
        if r["asn"] is None:
            continue
        by_asn.setdefault(r["asn"], []).append(r)

    def asn_avg(field):
        return {
            asn: sum((r[field] or 0) for r in rs) / len(rs)
            for asn, rs in by_asn.items()
        }

    risk_avg = asn_avg("risk_score")
    anom_avg = asn_avg("anomaly_score")

    def colored_asn_map(avg_map, filename, color_fn=color_for_score, legend_title="Avg Score", legend=LEGEND_SCORE):
        m = _base_map()
        for r in rows:
            asn = r["asn"]
            if asn is None or r["latitude"] is None:
                continue
            folium.CircleMarker(
                location=[r["latitude"], r["longitude"]],
                radius=4,
                color=color_fn(avg_map.get(asn, 0)),
                fill=True,
                fill_opacity=0.7,
                popup=f"ASN: {asn} ({r['as_org']})<br>Avg: {avg_map.get(asn, 0):.3f}",
            ).add_to(m)
        _add_legend(m, legend_title, legend)
        save_map(m, filename)

    colored_asn_map(risk_avg, "asn_risk_map.html", legend_title="Avg Risk")
    colored_asn_map(anom_avg, "asn_anomaly_map.html", color_fn=color_for_anomaly,
                     legend_title="Avg Anomaly", legend=LEGEND_ANOMALY)

    m = _base_map()
    _add_points(m, rows, lambda r: hex_for_cluster(r["asn"]),
                lambda r: f"ASN: {r['asn']} ({r['as_org']})",
                cluster=False)
    _add_legend(m, "ASN", [(None, "Each color = one ASN")])
    save_map(m, "asn_behavior_map.html")

    m = _base_map()
    _add_points(m, rows, lambda r: "#3388ff", lambda r: f"ASN: {r['asn']} ({r['as_org']})")
    save_map(m, "asn_density_map.html")
    save_map(m, "asn_density.html")

    # Per-ASN detail maps (asn_detail route)
    for asn, rs in by_asn.items():
        m = _base_map()
        _add_points(m, rs, lambda r: hex_for_cluster(r["cluster_id"]),
                    lambda r: f"Router: {r['router_hash']}",
                    cluster=False)
        _add_legend(m, "Behavioral Cluster", LEGEND_CLUSTER_ID)
        save_map(m, f"asn_{asn}_map.html")

        m = _base_map()
        _add_points(m, rs, lambda r: color_for_score(r["risk_score"]),
                    lambda r: f"Router: {r['router_hash']}<br>Risk: {r['risk_score']}",
                    cluster=False)
        _add_legend(m, "Risk Score", LEGEND_SCORE)
        save_map(m, f"asn_{asn}_risk_map.html")

        m = _base_map()
        _add_points(m, rs, lambda r: color_for_anomaly(r["anomaly_score"]),
                    lambda r: f"Router: {r['router_hash']}<br>Anomaly: {r['anomaly_score']}",
                    cluster=False)
        _add_legend(m, "Anomaly Score", LEGEND_ANOMALY)
        save_map(m, f"asn_{asn}_anomaly_map.html")

        # Distinct from asn_{asn}_map.html above -- that one already shows
        # cluster_id (which cluster each router belongs to). This one shows
        # cluster_distance (how far each router sits from its own cluster's
        # centroid, i.e. how behaviorally atypical it is for its peer
        # group), so the two maps carry genuinely different information
        # instead of duplicating the same coloring.
        m = _base_map()
        _add_points(m, rs, lambda r: color_for_score(r["cluster_distance"]),
                    lambda r: f"Router: {r['router_hash']}<br>Cluster: {r['cluster_id']}<br>Cluster distance: {r['cluster_distance']}",
                    cluster=False)
        _add_legend(m, "Cluster Distance", LEGEND_SCORE)
        save_map(m, f"asn_{asn}_behavior_map.html")


# --------------------------------------------
#   CLUSTER MAPS (behavioral KMeans cluster_id)
# --------------------------------------------

def _generate_cluster_maps(rows):
    print("--- Cluster maps ---")

    # cluster_map.html -- colored by cluster_id
    m = _base_map()
    _add_points(m, rows, lambda r: hex_for_cluster(r["cluster_id"]),
                lambda r: f"Router: {r['router_hash']}<br>Cluster: {r['cluster_id']}",
                cluster=False)
    _add_legend(m, "Behavioral Cluster", LEGEND_CLUSTER_ID)
    save_map(m, "cluster_map.html")

    # Genuine density heatmap of clustered routers -- was previously just a
    # duplicate save of cluster_map.html under a different filename.
    cluster_points = [[r["latitude"], r["longitude"]]
                       for r in rows if r.get("latitude") is not None]
    save_map(_heatmap(cluster_points), "cluster_heatmap.html")

    by_cluster = {}
    for r in rows:
        by_cluster.setdefault(r["cluster_id"], []).append(r)

    def cluster_avg(field):
        return {
            cid: sum((r[field] or 0) for r in rs) / len(rs)
            for cid, rs in by_cluster.items()
        }

    risk_avg = cluster_avg("risk_score")
    anom_avg = cluster_avg("anomaly_score")
    behav_avg = cluster_avg("cluster_distance")
    susp_avg = cluster_avg("suspiciousness")

    def colored_map(avg_map, color_map_fn, filename, legend_title="Avg Score", legend=LEGEND_SCORE):
        # No MarkerCluster wrapper -- clustering's own built-in bubble
        # coloring (based on marker count) was overriding the color_map_fn
        # coloring we set per-marker, so e.g. cluster_risk_map.html and
        # cluster_suspicious_map.html rendered as visually identical count
        # bubbles at low zoom despite genuinely different colors underneath.
        m = _base_map()
        for r in rows:
            cid = r["cluster_id"]
            avg = avg_map.get(cid, 0)
            folium.CircleMarker(
                location=[r["latitude"], r["longitude"]],
                radius=4,
                color=color_map_fn(avg),
                fill=True,
                fill_opacity=0.7,
                popup=f"Cluster: {cid}<br>Avg: {avg:.3f}",
            ).add_to(m)
        _add_legend(m, legend_title, legend)
        save_map(m, filename)

    colored_map(risk_avg, color_for_score, "cluster_risk_map.html", legend_title="Avg Risk")
    colored_map(anom_avg, color_for_anomaly, "cluster_anomaly_map.html",
                legend_title="Avg Anomaly", legend=LEGEND_ANOMALY)
    colored_map(behav_avg, color_for_score, "cluster_behavior_map.html", legend_title="Avg Cluster Distance")
    colored_map(susp_avg, color_for_score, "cluster_suspicious_map.html", legend_title="Avg Suspiciousness")

    # --- per-cluster dynamic maps (cluster_detail route) ---
    for cid, rs in by_cluster.items():
        try:
            m = _base_map()
            _add_points(m, rs, lambda r: hex_for_cluster(r["cluster_id"]),
                        lambda r: f"Router: {r['router_hash']}",
                        cluster=False)
            _add_legend(m, "Behavioral Cluster", LEGEND_CLUSTER_ID)
            save_map(m, f"cluster_{cid}_map.html")

            m = _base_map()
            _add_points(m, rs, lambda r: color_for_score(r["risk_score"]),
                        lambda r: f"Router: {r['router_hash']}<br>Risk: {r['risk_score']}",
                        cluster=False)
            _add_legend(m, "Risk Score", LEGEND_SCORE)
            save_map(m, f"cluster_{cid}_risk_map.html")

            m = _base_map()
            _add_points(m, rs, lambda r: color_for_anomaly(r["anomaly_score"]),
                        lambda r: f"Router: {r['router_hash']}<br>Anomaly: {r['anomaly_score']}",
                        cluster=False)
            _add_legend(m, "Anomaly Score", LEGEND_ANOMALY)
            save_map(m, f"cluster_{cid}_anomaly_map.html")

            m = _base_map()
            _add_points(m, rs, lambda r: color_for_score(r["cluster_distance"]),
                        lambda r: f"Router: {r['router_hash']}<br>Distance: {r['cluster_distance']}",
                        cluster=False)
            _add_legend(m, "Cluster Distance", LEGEND_SCORE)
            save_map(m, f"cluster_{cid}_behavior_map.html")
        except Exception:
            import traceback
            traceback.print_exc()


# --------------------------------------------
#   PURE GEOGRAPHIC CLUSTER MAP (DBSCAN, ml/geo_clustering.py)
# --------------------------------------------
# Distinct from cluster_map.html above: that one colors by the mixed-feature
# behavioral KMeans cluster_id, this one colors by geo_cluster_id, produced
# by DBSCAN clustering purely on (latitude, longitude) -- i.e. genuine
# geographic hot-spots, with -1 meaning "not part of any dense cluster."

def _generate_geo_cluster_map(rows):
    print("--- Geographic cluster map (DBSCAN) ---")

    if not rows:
        print("[WARN] no geo cluster data available, skipping geo_cluster_map.html")
        return

    # No MarkerCluster wrapper -- see colored_map() above for why: its
    # count-based bubble coloring masks the actual geo_cluster_id coloring.
    m = folium.Map(location=[20, 0], zoom_start=2, tiles="OpenStreetMap")
    for router_hash, geo_cluster_id, lat, lon in rows:
        color = "#888888" if geo_cluster_id == -1 else hex_for_cluster(geo_cluster_id)
        label = "noise/isolated" if geo_cluster_id == -1 else f"geo cluster {geo_cluster_id}"
        folium.CircleMarker(
            location=[lat, lon],
            radius=4,
            color=color,
            fill=True,
            fill_opacity=0.7,
            popup=f"Router: {router_hash}<br>{label}",
        ).add_to(m)
    _add_legend(m, "Geographic Cluster (DBSCAN)", LEGEND_GEO_CLUSTER)
    save_map(m, "geo_cluster_map.html")


# --------------------------------------------
#   ROUTER-LEVEL MAPS (per router_hash)
# --------------------------------------------

def _generate_router_maps(router_dict, movement_dict, limit=None):
    print("--- Router-level maps ---")

    rows = list(router_dict.values())

    # Global router-level aggregate views
    m = _base_map()
    _add_points(m, rows, lambda r: color_for_score(r["risk_score"]),
                lambda r: f"Router: {r['router_hash']}<br>Risk: {r['risk_score']}",
                cluster=False)
    _add_legend(m, "Risk Score", LEGEND_SCORE)
    save_map(m, "router_risk_map.html")

    # router_risk_anomaly_map.html and router_risk_behavior_map.html used to
    # be colored by risk_score alone too -- identical to router_risk_map.html
    # in everything but popup text. Now each genuinely combines two
    # dimensions in one marker: fill color = risk, border color = the
    # second dimension, so a router that's notable on both stands out
    # distinctly from one that's only notable on one.
    m = _base_map()
    for r in rows:
        if r.get("latitude") is None:
            continue
        folium.CircleMarker(
            location=[r["latitude"], r["longitude"]],
            radius=5,
            color=color_for_anomaly(r["anomaly_score"]),
            weight=3,
            fill=True,
            fill_color=color_for_score(r["risk_score"]),
            fill_opacity=0.8,
            popup=f"Router: {r['router_hash']}<br>Risk: {r['risk_score']}<br>Anomaly: {r['anomaly_score']}",
        ).add_to(m)
    _add_legend(m, "Fill = Risk, Border = Anomaly", LEGEND_SCORE)
    save_map(m, "router_risk_anomaly_map.html")

    m = _base_map()
    for r in rows:
        if r.get("latitude") is None:
            continue
        folium.CircleMarker(
            location=[r["latitude"], r["longitude"]],
            radius=5,
            color=hex_for_cluster(r["cluster_id"]),
            weight=3,
            fill=True,
            fill_color=color_for_score(r["risk_score"]),
            fill_opacity=0.8,
            popup=f"Router: {r['router_hash']}<br>Risk: {r['risk_score']}<br>Cluster: {r['cluster_id']}",
        ).add_to(m)
    _add_legend(m, "Fill = Risk, Border = Cluster", LEGEND_SCORE)
    save_map(m, "router_risk_behavior_map.html")

    # NOTE: router_risk_heatmap.html is the genuine density heatmap built in
    # _generate_global_maps() -- not duplicated here.

    # Per-router detail maps (router_detail route)
    items = list(router_dict.items())
    if limit:
        items = items[:limit]

    for router_hash, r in items:
        try:
            m = _base_map()
            folium.Marker(
                location=[r["latitude"], r["longitude"]],
                popup=f"Router: {router_hash}",
            ).add_to(m)
            save_map(m, f"router_{router_hash}_pin_map.html")

            points = movement_dict.get(router_hash, [])
            m = _base_map()
            if len(points) >= 2:
                folium.PolyLine(
                    locations=[(lat, lon) for lat, lon, _ in points],
                    color="blue", weight=2, opacity=0.7,
                ).add_to(m)
                for lat, lon, ts in points:
                    folium.CircleMarker(location=[lat, lon], radius=3, color="blue",
                                         fill=True, popup=str(ts)).add_to(m)
            elif r["latitude"] is not None:
                folium.Marker(location=[r["latitude"], r["longitude"]],
                               popup=f"Router: {router_hash}").add_to(m)
            save_map(m, f"router_{router_hash}_movement_map.html")

            m = _base_map()
            folium.CircleMarker(
                location=[r["latitude"], r["longitude"]], radius=6,
                color=color_for_score(r["risk_score"]), fill=True, fill_opacity=0.8,
                popup=f"Risk: {r['risk_score']}",
            ).add_to(m)
            save_map(m, f"router_{router_hash}_risk_map.html")

            m = _base_map()
            folium.CircleMarker(
                location=[r["latitude"], r["longitude"]], radius=6,
                color=color_for_anomaly(r["anomaly_score"]), fill=True, fill_opacity=0.8,
                popup=f"Anomaly: {r['anomaly_score']}",
            ).add_to(m)
            save_map(m, f"router_{router_hash}_anomaly_map.html")

            m = _base_map()
            folium.CircleMarker(
                location=[r["latitude"], r["longitude"]], radius=6,
                color=hex_for_cluster(r["cluster_id"]), fill=True, fill_opacity=0.8,
                popup=f"Cluster: {r['cluster_id']}",
            ).add_to(m)
            save_map(m, f"router_{router_hash}_behavior_map.html")

            m = _base_map()
            folium.CircleMarker(
                location=[r["latitude"], r["longitude"]], radius=6,
                color=color_for_score(r["suspiciousness"]), fill=True, fill_opacity=0.8,
                popup=f"Suspiciousness: {r['suspiciousness']}",
            ).add_to(m)
            save_map(m, f"router_{router_hash}_suspicious_map.html")

            m = _base_map()
            folium.CircleMarker(
                location=[r["latitude"], r["longitude"]], radius=6,
                color=color_for_score(r["cluster_distance"]), fill=True, fill_opacity=0.8,
                popup=f"Cluster distance: {r['cluster_distance']}",
            ).add_to(m)
            save_map(m, f"router_{router_hash}_cluster_distance_map.html")

            m = _base_map()
            folium.CircleMarker(
                location=[r["latitude"], r["longitude"]], radius=6,
                color=color_for_score(r["risk_score"]), fill=True, fill_opacity=0.8,
                popup=f"Risk: {r['risk_score']}<br>Anomaly: {r['anomaly_score']}",
            ).add_to(m)
            save_map(m, f"router_{router_hash}_risk_anomaly_map.html")

            m = _base_map()
            folium.CircleMarker(
                location=[r["latitude"], r["longitude"]], radius=6,
                color=color_for_score(r["risk_score"]), fill=True, fill_opacity=0.8,
                popup=f"Risk: {r['risk_score']}<br>Cluster: {r['cluster_id']}",
            ).add_to(m)
            save_map(m, f"router_{router_hash}_risk_behavior_map.html")

            # Genuine (single-point) density heatmap -- renders as a soft
            # glow rather than a hard-edged marker, visually distinct from
            # router_{hash}_risk_map.html above.
            heat_point = [[r["latitude"], r["longitude"], max(r["risk_score"] or 0, 0.05)]]
            save_map(_heatmap(heat_point, radius=25, blur=25), f"router_{router_hash}_risk_heatmap.html")

        except Exception:
            import traceback
            traceback.print_exc()


# --------------------------------------------
#   DASHBOARD ALIASES
# --------------------------------------------
# A few dashboard views link to friendlier filenames that are really just
# the same underlying map as one already generated above.

def _generate_dashboard_aliases():
    print("--- Dashboard aliases ---")
    import shutil

    aliases = {
        "router_distribution.html": "global_map.html",
    }
    for alias, source in aliases.items():
        src_path = os.path.join(MAP_DIR, source)
        dst_path = os.path.join(MAP_DIR, alias)
        if os.path.exists(src_path):
            shutil.copyfile(src_path, dst_path)
            print(f"[MAP] {alias} (alias of {source})")
