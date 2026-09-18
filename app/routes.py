# ============================================================
# MODULE 1 — CORE SETUP / UTILITIES / DB CONNECTION
# ============================================================

from flask import Blueprint, render_template, request, send_from_directory, redirect, url_for, Response
bp = Blueprint("main", __name__)
import duckdb
import os
import math

from flask import send_from_directory

# Absolute path, independent of the process's CWD at launch time (send_from_directory
# resolves relative paths against the app package's root_path, not the CWD, which
# silently 404'd every /maps/view/... and /maps/<name> route before this fix).
BASE_DIR = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
VIS_PATH = os.path.join(BASE_DIR, "visualizations")
MAPS_PATH = os.path.join(VIS_PATH, "maps")

# Listing the maps directory means stat-ing 305k files -- ~3s, which it spent on
# every single /maps request. The directory only changes when the generator runs,
# so cache the sorted listing and invalidate on the directory's mtime (adding or
# removing a file bumps it), keeping newly generated maps visible.
_maps_cache = {"mtime": None, "files": []}

def list_map_files():
    try:
        mtime = os.stat(MAPS_PATH).st_mtime
    except OSError:
        return []
    if _maps_cache["mtime"] != mtime:
        _maps_cache["files"] = sorted(
            f for f in os.listdir(MAPS_PATH) if f.endswith(".html")
        )
        _maps_cache["mtime"] = mtime
    return _maps_cache["files"]

@bp.route("/visualizations/charts/<path:filename>")
def serve_chart(filename):
    return send_from_directory(os.path.join(VIS_PATH, "charts"), filename)

@bp.route("/visualizations/maps/<path:filename>")
def serve_map(filename):
    return send_from_directory(os.path.join(VIS_PATH, "maps"), filename)

# ------------------------------------------------------------
# Helper object: dict with attribute-style access
# ------------------------------------------------------------
class Obj(dict):
    __getattr__ = dict.get
    __setattr__ = dict.__setitem__

# ------------------------------------------------------------
# Database connection (persistent)
# ------------------------------------------------------------
from database.db import get_conn
# ------------------------------------------------------------
# Safe query wrapper
# ------------------------------------------------------------
def q(sql, params=None):
    # Every call previously opened a fresh connection via get_conn() and
    # never closed it -- each dashboard page load fires many of these, so
    # they accumulated as open file handles on the DB for the lifetime of
    # the dashboard process. DuckDB requires exclusive access for writers,
    # so enough leaked read connections eventually blocked every external
    # script (pipeline runs, manual analysis scripts) from writing at all.
    con = get_conn(read_only=True)
    try:
        if params:
            return con.execute(sql, params).fetchall()
        return con.execute(sql).fetchall()
    finally:
        con.close()

# ------------------------------------------------------------
# Pagination helper
# ------------------------------------------------------------
def paginate(total_items, page, per_page=50):
    total_pages = max(1, math.ceil(total_items / per_page))
    page = max(1, min(page, total_pages))
    offset = (page - 1) * per_page
    return page, total_pages, offset, per_page

# ------------------------------------------------------------
# Filter extraction helper
# ------------------------------------------------------------
def extract_filters(request_args, allowed_keys):
    """Extract only allowed filter keys from request args."""
    f = {}
    for key in allowed_keys:
        val = request_args.get(key)
        if val not in (None, ""):
            f[key] = val
    return f

# ------------------------------------------------------------
# Pagination link query-string helper
# ------------------------------------------------------------
# `filters` never contains "page" (extract_filters only pulls the allowed
# filter keys), so this is safe to append after a fresh page=N in Prev/Next
# links without ever duplicating/stacking the page param.
from urllib.parse import urlencode

def filters_qs(filters):
    return urlencode(filters)

# ------------------------------------------------------------
# Static file serving for charts/maps
# ------------------------------------------------------------
@bp.route("/visualizations/<path:filename>")
def serve_visualization(filename):
    return send_from_directory(VIS_PATH, filename)

# ------------------------------------------------------------
# Root redirect → dashboard
# ------------------------------------------------------------
@bp.route("/")
def index():
    return redirect(url_for("main.dashboard"))

# ------------------------------------------------------------
# Health check
# ------------------------------------------------------------
from database.db import DB_PATH

@bp.route("/health")
def health():
    return {"status": "ok", "db": os.path.exists(DB_PATH)}

# ============================================================
# MODULE 2 — DASHBOARD + GLOBAL INTELLIGENCE
# ============================================================

# ------------------------------------------------------------
# Helper: build global intelligence summary
# ------------------------------------------------------------
def build_global_intel():
    # Total routers
    total_routers = q("SELECT COUNT(*) FROM enriched_router_data")[0][0]

    # Total ASNs
    asn_count = q("SELECT COUNT(DISTINCT asn) FROM enriched_router_data")[0][0]

    # Total countries
    country_count = q("SELECT COUNT(DISTINCT country_geo) FROM enriched_router_data")[0][0]

    # Total clusters
    cluster_count = q("SELECT COUNT(DISTINCT cluster_id) FROM router_behavior")[0][0]

    # Avg risk (NEW PIPELINE)
    avg_risk = q("SELECT AVG(risk_score) FROM router_features")[0][0] or 0.0

    # Avg suspiciousness (NEW PIPELINE)
    avg_susp = q("SELECT AVG(suspiciousness) FROM router_features")[0][0] or 0.0

    # High‑risk routers (real risk_score range is ~0.06-0.60; 0.45+ is the top band)
    high_risk = q("""
        SELECT COUNT(*)
        FROM router_features
        WHERE risk_score >= 0.45
    """)[0][0]

    # Suspicious routers (real suspiciousness range is ~0.05-0.46; 0.30+ is the top band)
    suspicious = q("""
        SELECT COUNT(*)
        FROM router_features
        WHERE suspiciousness >= 0.30
    """)[0][0]

    # Top ASN
    top_asn_row = q("""
        SELECT asn, COUNT(*) AS c
        FROM enriched_router_data
        GROUP BY asn
        ORDER BY c DESC
        LIMIT 1
    """)
    # NULL legitimately can win this ranking (routers whose IP didn't
    # resolve to an ASN) -- kept in the ranking as honest data, just
    # labeled clearly instead of showing Python's raw "None".
    top_asn = (top_asn_row[0][0] if top_asn_row and top_asn_row[0][0] is not None
               else "Unresolved (no ASN match)")

    # Top country
    top_country_row = q("""
        SELECT country_geo, COUNT(*) AS c
        FROM enriched_router_data
        GROUP BY country_geo
        ORDER BY c DESC
        LIMIT 1
    """)
    top_country = (top_country_row[0][0] if top_country_row and top_country_row[0][0] is not None
                   else "Unresolved (no geolocation)")

    # Most risky cluster (NEW PIPELINE)
    top_cluster_row = q("""
        SELECT b.cluster_id, AVG(f.risk_score) AS avg_risk
        FROM router_behavior b
        JOIN router_features f USING (router_hash)
        GROUP BY b.cluster_id
        ORDER BY avg_risk DESC
        LIMIT 1
    """)
    top_cluster = top_cluster_row[0][0] if top_cluster_row else "N/A"

    # Behavioral outliers (post-rebuild cluster_distance range is ~0.003-5.8, avg ~0.26; 1.0+ is the top band)
    behavior_outliers = q("""
        SELECT COUNT(*)
        FROM router_behavior
        WHERE cluster_distance >= 1.0
    """)[0][0]

    # Single-operator/family concentration check: what fraction of all
    # routers sit in just the top 5 ASNs? "Geographic clustering" findings
    # are only meaningful if they reflect real regional adoption rather
    # than one operator running many routers under a handful of ASNs --
    # this makes that check explicit and visible instead of a one-off
    # manual query.
    top5_asn_count = q("""
        SELECT SUM(c) FROM (
            SELECT COUNT(*) AS c
            FROM enriched_router_data
            WHERE asn IS NOT NULL
            GROUP BY asn
            ORDER BY c DESC
            LIMIT 5
        )
    """)[0][0] or 0
    total_with_asn = q("SELECT COUNT(*) FROM enriched_router_data WHERE asn IS NOT NULL")[0][0] or 1
    top5_asn_concentration_pct = 100.0 * top5_asn_count / total_with_asn

    return Obj(
        total_routers=total_routers,
        total_asns=asn_count,
        total_countries=country_count,
        total_clusters=cluster_count,
        avg_risk=avg_risk,
        avg_susp=avg_susp,
        high_risk=high_risk,
        suspicious=suspicious,
        top_asn=top_asn,
        top_country=top_country,
        top_cluster=top_cluster,
        behavior_outliers=behavior_outliers,
        top5_asn_concentration_pct=top5_asn_concentration_pct
    )

# ============================================================
# DASHBOARD ROUTE — UPDATED FOR NEW PIPELINE
# ============================================================
@bp.route("/dashboard")
def dashboard():

    # ============================
    # GLOBAL INTELLIGENCE SUMMARY
    # ============================
    total_routers = q("SELECT COUNT(*) FROM router_behavior")[0][0]
    total_asns = q("SELECT COUNT(DISTINCT asn) FROM enriched_router_data")[0][0]
    total_countries = q("SELECT COUNT(DISTINCT country_geo) FROM enriched_router_data")[0][0]
    total_clusters = q("SELECT COUNT(DISTINCT cluster_id) FROM router_behavior")[0][0]

    avg_risk = q("SELECT COALESCE(AVG(risk_score), 0.0) FROM router_features")[0][0]
    avg_susp = q("SELECT COALESCE(AVG(suspiciousness), 0.0) FROM router_features")[0][0]
    avg_uptime_slope = q("SELECT COALESCE(AVG(uptime_hours), 0.0) FROM router_features")[0][0]
    avg_entropy, avg_periodicity, avg_stability = [
        x or 0.0 for x in q("""
            SELECT AVG(entropy), AVG(periodicity), AVG(stability) FROM router_behavior
        """)[0]
    ]

    # Real ranges: risk_score ~0.06-0.60, suspiciousness ~0.05-0.46, entropy ~0.7-71 (avg ~17)
    high_risk = q("SELECT COUNT(*) FROM router_features WHERE risk_score >= 0.45")[0][0]
    suspicious = q("SELECT COUNT(*) FROM router_features WHERE suspiciousness >= 0.30")[0][0]
    unstable = q("SELECT COUNT(*) FROM router_behavior WHERE COALESCE(entropy, 0) > 30")[0][0]

    top_asn_row = q("""
        SELECT asn, COUNT(*) AS c
        FROM enriched_router_data
        GROUP BY asn
        ORDER BY c DESC
        LIMIT 1
    """)
    # NULL legitimately can (and currently does) win this ranking -- that's
    # real, honest data (routers whose IP didn't resolve to an ASN), not an
    # error, so it's kept in the ranking rather than filtered out. Just
    # label it clearly instead of showing Python's raw "None".
    top_asn = (top_asn_row[0][0] if top_asn_row and top_asn_row[0][0] is not None
               else "Unresolved (no ASN match)")

    top_country_row = q("""
        SELECT country_geo, COUNT(*) AS c
        FROM enriched_router_data
        GROUP BY country_geo
        ORDER BY c DESC
        LIMIT 1
    """)
    top_country = (top_country_row[0][0] if top_country_row and top_country_row[0][0] is not None
                   else "Unresolved (no geolocation)")

    top_cluster_row = q("""
        SELECT b.cluster_id, COALESCE(AVG(f.risk_score), 0.0) AS avg_risk
        FROM router_behavior b
        JOIN router_features f USING (router_hash)
        GROUP BY b.cluster_id
        ORDER BY avg_risk DESC
        LIMIT 1
    """)

    top_cluster = top_cluster_row[0][0] if top_cluster_row else "N/A"

    # Single-operator/family concentration check: what fraction of all
    # routers sit in just the top 5 ASNs? "Geographic clustering" findings
    # are only meaningful if they reflect real regional adoption rather
    # than one operator running many routers under a handful of ASNs.
    top5_asn_count = q("""
        SELECT SUM(c) FROM (
            SELECT COUNT(*) AS c
            FROM enriched_router_data
            WHERE asn IS NOT NULL
            GROUP BY asn
            ORDER BY c DESC
            LIMIT 5
        )
    """)[0][0] or 0
    total_with_asn = q("SELECT COUNT(*) FROM enriched_router_data WHERE asn IS NOT NULL")[0][0] or 1
    top5_asn_concentration_pct = 100.0 * top5_asn_count / total_with_asn

    global_intel = Obj(
        total_routers=total_routers,
        total_asns=total_asns,
        total_countries=total_countries,
        total_clusters=total_clusters,
        avg_risk=avg_risk,
        avg_susp=avg_susp,
        avg_uptime_slope=avg_uptime_slope,
        avg_entropy=avg_entropy,
        avg_periodicity=avg_periodicity,
        avg_stability=avg_stability,
        high_risk=high_risk,
        suspicious=suspicious,
        unstable=unstable,
        top_asn=top_asn,
        top_country=top_country,
        top_cluster=top_cluster,
        top5_asn_concentration_pct=top5_asn_concentration_pct
    )

    # ============================
    # TOP ASN TABLES
    # ============================
    top_asns = [
        Obj(asn=row[0], as_org=row[1], count=row[2])
        for row in q("""
            SELECT asn, as_org, COUNT(*) AS c
            FROM enriched_router_data
            GROUP BY asn, as_org
            ORDER BY c DESC
            LIMIT 20
        """)
    ]

    top_asn_risk = [
        Obj(asn=row[0], as_org=row[1], risk=row[2])
        for row in q("""
            SELECT e.asn, e.as_org, AVG(f.risk_score) AS r
            FROM router_features f
            JOIN enriched_router_data e USING (router_hash)
            GROUP BY e.asn, e.as_org
            ORDER BY r DESC
            LIMIT 20
        """)
    ]

    top_asn_anomaly = [
        Obj(asn=row[0], as_org=row[1], anomaly=row[2])
        for row in q("""
            SELECT e.asn, e.as_org, AVG(f.anomaly_score) AS a
            FROM router_features f
            JOIN enriched_router_data e USING (router_hash)
            GROUP BY e.asn, e.as_org
            ORDER BY a ASC
            LIMIT 20
        """)
        # anomaly_score comes from sklearn IsolationForest.decision_function(),
        # where LOWER (more negative) = more anomalous, not higher -- ORDER BY
        # DESC here previously surfaced the *least* anomalous ASNs (score
        # exactly 0.0, i.e. completely normal) instead of the intended "top
        # anomalous" ranking.
    ]

    # ============================
    # TOP RISKY ROUTERS
    # ============================
    risky_rows = q("""
        SELECT 
            e.router_hash,
            f.risk_score,
            f.suspiciousness,
            f.anomaly_score,
            f.uptime_hours AS uptime_slope,
            b.cluster_id,
            b.cluster_distance,
            e.asn,
            e.country_geo
        FROM router_features f
        JOIN enriched_router_data e USING (router_hash)
        JOIN router_behavior b USING (router_hash)
        ORDER BY f.risk_score DESC
        LIMIT 100
    """)

    top_risky_routers = [
        Obj(
            router_hash=row[0],
            risk_score=row[1],
            suspiciousness=row[2],
            anomaly_score=row[3],
            uptime_slope=row[4],
            cluster_id=row[5],
            cluster_distance=row[6],
            asn=row[7],
            country=row[8]
        )
        for row in risky_rows
    ]

    # ============================
    # TOP 100 ROUTERS BY BEHAVIORAL DISTANCE
    # ============================
    # dashboard.html has always looped over "top_behavior_routers" for this
    # table, but nothing ever built or passed that variable -- Jinja treats
    # an undefined loop variable as empty rather than erroring, so the table
    # silently rendered with headers only and zero rows.
    top_behavior_routers = [
        Obj(
            router_hash=row[0],
            cluster_id=row[1],
            cluster_distance=row[2] or 0.0,
            entropy=row[3] or 0.0,
            periodicity=row[4] or 0.0,
            stability=row[5] or 0.0,
            asn=row[6],
            country=row[7]
        )
        for row in q("""
            SELECT
                b.router_hash,
                b.cluster_id,
                b.cluster_distance,
                b.entropy,
                b.periodicity,
                b.stability,
                e.asn,
                e.country_geo
            FROM router_behavior b
            JOIN enriched_router_data e USING (router_hash)
            ORDER BY b.cluster_distance DESC
            LIMIT 100
        """)
    ]

    # ============================
    # CLUSTER SUMMARY
    # ============================
    cluster_summary = [
        Obj(
            cluster_id=row[0],
            count=row[1],
            avg_risk=row[2] or 0.0,
            avg_suspiciousness=row[3] or 0.0,
            avg_entropy=row[4] or 0.0,
            avg_periodicity=row[5] or 0.0,
            avg_stability=row[6] or 0.0,
            avg_uptime_slope=row[7] or 0.0,
            avg_anomaly_score=row[8] or 0.0,
            avg_cluster_distance=row[9] or 0.0
        )
        for row in q("""
            SELECT
                b.cluster_id,
                COUNT(*) AS count,
                AVG(f.risk_score) AS avg_risk,
                AVG(f.suspiciousness) AS avg_suspiciousness,
                AVG(b.entropy) AS avg_entropy,
                AVG(b.periodicity) AS avg_periodicity,
                AVG(b.stability) AS avg_stability,
                AVG(f.uptime_hours) AS avg_uptime_slope,
                AVG(f.anomaly_score) AS avg_anomaly_score,
                AVG(b.cluster_distance) AS avg_cluster_distance
            FROM router_behavior b
            JOIN router_features f USING (router_hash)
            GROUP BY b.cluster_id
            ORDER BY b.cluster_id
        """)
    ]


    # ============================
    # GLOBAL TIMESERIES
    # ============================
    # A single pipeline run genuinely takes 8-14 minutes end to end
    # (confirmed directly from launchd_pipeline.log run timestamps), and
    # router_snapshot_writer.py stamps each router with "now" at the moment
    # it's individually parsed -- so one run's writes are for real spread
    # across several calendar minutes, not clustered in one. Bucketing by
    # raw minute (the previous approach) chopped a single logical run into
    # many small, uneven fragments -- e.g. one real run showing as 58 routers
    # in one minute and 994 in another a few minutes later, which reads as
    # wild network volatility but is actually one coherent run sliced
    # arbitrarily. Grouping by gap instead (a new "run" starts whenever more
    # than 10 minutes elapse since the previous timestamp -- runs are ~30
    # min apart on schedule, so this comfortably separates real runs without
    # assuming a fixed clock grid, which matters since manual runs don't
    # land on the schedule at all) gives one point per actual run.
    ts_global = [
        Obj(timestamp=row[0].strftime("%Y-%m-%d %H:%M"), total_routers=row[1])
        for row in q("""
            WITH ordered AS (
                SELECT
                    router_hash,
                    timestamp,
                    LAG(timestamp) OVER (ORDER BY timestamp) AS prev_ts
                FROM timeseries_router_stats
            ),
            run_boundaries AS (
                SELECT
                    router_hash,
                    timestamp,
                    CASE
                        WHEN prev_ts IS NULL OR timestamp - prev_ts > INTERVAL 10 MINUTE
                        THEN 1 ELSE 0
                    END AS is_new_run
                FROM ordered
            ),
            runs AS (
                SELECT
                    router_hash,
                    timestamp,
                    SUM(is_new_run) OVER (ORDER BY timestamp) AS run_id
                FROM run_boundaries
            ),
            run_buckets AS (
                SELECT
                    run_id,
                    MIN(timestamp) AS bucket,
                    COUNT(DISTINCT router_hash) AS total_routers
                FROM runs
                GROUP BY run_id
                ORDER BY bucket DESC
                LIMIT 500
            )
            SELECT bucket, total_routers FROM run_buckets
            ORDER BY bucket ASC
        """)
    ]

    # ============================
    # STATIC CHART FILES
    # ============================
    charts_path = os.path.join(VIS_PATH, "charts")
    files = os.listdir(charts_path) if os.path.exists(charts_path) else []

    return render_template(
        "dashboard.html",
        global_intel=global_intel,
        top_asns=top_asns,
        top_asn_risk=top_asn_risk,
        top_asn_anomaly=top_asn_anomaly,
        top_risky_routers=top_risky_routers,
        top_behavior_routers=top_behavior_routers,
        cluster_summary=cluster_summary,
        ts_global=ts_global,
        files=files
    )

# ------------------------------------------------------------
# Global Intelligence Page
# ------------------------------------------------------------
@bp.route("/global")
def global_page():
    intel = build_global_intel()   # must be updated to use new pipeline

    # Top countries
    top_countries = q("""
        SELECT country_geo, COUNT(*) AS c
        FROM enriched_router_data
        GROUP BY country_geo
        ORDER BY c DESC
        LIMIT 20
    """)

    # Top ASNs
    top_asns = q("""
        SELECT asn, as_org, COUNT(*) AS c
        FROM enriched_router_data
        GROUP BY asn, as_org
        ORDER BY c DESC
        LIMIT 20
    """)

    # Top clusters
    top_clusters = q("""
        SELECT cluster_id, COUNT(*) AS c
        FROM router_behavior
        GROUP BY cluster_id
        ORDER BY c DESC
        LIMIT 20
    """)

    # Behavioral metrics (NEW PIPELINE)
    avg_entropy = q("SELECT COALESCE(AVG(entropy), 0.0) FROM router_behavior")[0][0]
    avg_periodicity = q("SELECT COALESCE(AVG(periodicity), 0.0) FROM router_behavior")[0][0]
    avg_stability = q("SELECT COALESCE(AVG(stability), 0.0) FROM router_behavior")[0][0]
    avg_uptime_slope = q("SELECT AVG(uptime_hours) FROM router_features")[0][0] or 0.0
    avg_cluster_distance = q("SELECT COALESCE(AVG(cluster_distance), 0.0) FROM router_behavior")[0][0]

    return render_template(
        "global.html",
        total_routers=intel.total_routers,
        asn_count=intel.total_asns,
        country_count=intel.total_countries,
        cluster_count=intel.total_clusters,
        avg_risk=intel.avg_risk,
        avg_suspicious=intel.avg_susp,
        high_risk_count=intel.high_risk,
        suspicious_count=intel.suspicious,
        top_asn=intel.top_asn,
        top_country=intel.top_country,
        top_cluster=intel.top_cluster,
        behavior_outliers=intel.behavior_outliers,
        avg_entropy=avg_entropy,
        avg_periodicity=avg_periodicity,
        avg_stability=avg_stability,
        avg_uptime_slope=avg_uptime_slope,
        avg_cluster_distance=avg_cluster_distance,
        top_countries=top_countries,
        top_asns=top_asns,
        top_clusters=top_clusters
    )

# ============================================================
# MODULE 3 — ROUTERS + ROUTER DETAIL
# ============================================================

# ------------------------------------------------------------
# Helper: build router intelligence object
# ------------------------------------------------------------
def build_router_info(router_hash):

    # --------------------------------------------------------
    # BASE METADATA (ASN, country, version)
    # --------------------------------------------------------
    meta_row = q("""
        SELECT 
            asn, as_org, country_geo,
            version_major, version_minor, version_patch
        FROM enriched_router_data
        WHERE router_hash = ?
        LIMIT 1
    """, [router_hash])

    if not meta_row:
        return None

    asn, as_org, country, vmaj, vmin, vpatch = meta_row[0]

    # --------------------------------------------------------
    # LATEST FEATURES (risk, suspiciousness, anomaly, uptime)
    # --------------------------------------------------------
    feat_row = q("""
        SELECT
            risk_score,
            suspiciousness,
            anomaly_score,
            uptime_hours
        FROM router_features
        WHERE router_hash = ?
        LIMIT 1
    """, [router_hash])

    if feat_row:
        risk_score, suspiciousness, anomaly_score, uptime_hours = feat_row[0]
    else:
        risk_score = suspiciousness = anomaly_score = uptime_hours = 0.0
    ts = None

    # --------------------------------------------------------
    # BEHAVIOR METRICS
    # --------------------------------------------------------
    beh_row = q("""
        SELECT 
            cluster_id,
            entropy,
            periodicity,
            stability,
            cluster_distance
        FROM router_behavior
        WHERE router_hash = ?
        LIMIT 1
    """, [router_hash])

    if beh_row:
        cluster_id, entropy, periodicity, stability, cluster_distance = beh_row[0]
        # entropy/periodicity are genuinely NULL for routers with fewer
        # than 3 observations (not enough history yet) -- 0.0 is a display
        # fallback, not a claim of zero entropy/periodicity.
        entropy = entropy or 0.0
        periodicity = periodicity or 0.0
        stability = stability or 0.0
        cluster_distance = cluster_distance or 0.0
    else:
        cluster_id = None
        entropy = periodicity = stability = cluster_distance = 0.0

    # --------------------------------------------------------
    # RISK EXPLANATION (NEW PIPELINE)
    # --------------------------------------------------------
    if risk_score >= 0.45:
        risk_explanation = "High risk: strong behavioral or statistical deviation."
    elif risk_score >= 0.30:
        risk_explanation = "Moderate risk: some unusual characteristics detected."
    else:
        risk_explanation = "Low risk: router appears normal."

    # --------------------------------------------------------
    # BUILD ROUTER INTELLIGENCE OBJECT
    # --------------------------------------------------------
    return Obj(
        router_hash=router_hash,
        asn=asn,
        as_org=as_org,
        country=country,
        version=f"{vmaj}.{vmin}.{vpatch}",

        uptime_hours=uptime_hours,
        timestamp=ts,

        risk_score=risk_score,
        suspiciousness=suspiciousness,
        anomaly_score=anomaly_score,

        behavior_cluster=cluster_id,
        entropy=entropy,
        periodicity=periodicity,
        stability=stability,
        uptime_slope=uptime_hours,  # template compatibility
        cluster_distance=cluster_distance,

        risk_explanation=risk_explanation
    )

# ============================================================
# ROUTERS PAGE — FULLY CORRECTED
# ============================================================
@bp.route("/routers")
def routers():
    allowed = [
        "asn", "country", "cluster",
        "min_susp", "max_susp",
        "min_risk", "max_risk",
        "min_anomaly", "max_anomaly",
        "search"
    ]
    filters = extract_filters(request.args, allowed)

    # ============================
    # BASE QUERY (NEW PIPELINE)
    # ============================
    sql = """
        SELECT 
            e.router_hash,
            f.risk_score,
            f.suspiciousness,
            f.anomaly_score,
            b.cluster_id,
            b.entropy,
            b.periodicity,
            b.stability,
            f.uptime_hours AS uptime_slope,
            b.cluster_distance,
            e.asn,
            e.country_geo,
            e.version_major, e.version_minor, e.version_patch
        FROM enriched_router_data e
        JOIN router_features f USING (router_hash)
        LEFT JOIN router_behavior b USING (router_hash)
        WHERE 1=1
    """
    params = []

    # ============================
    # FILTERS
    # ============================
    if "asn" in filters:
        sql += " AND e.asn = ?"
        params.append(filters["asn"])

    if "country" in filters:
        sql += " AND e.country_geo = ?"
        params.append(filters["country"])

    if "cluster" in filters:
        sql += " AND b.cluster_id = ?"
        params.append(filters["cluster"])

    if "min_susp" in filters:
        sql += " AND f.suspiciousness >= ?"
        params.append(float(filters["min_susp"]))

    if "max_susp" in filters:
        sql += " AND f.suspiciousness <= ?"
        params.append(float(filters["max_susp"]))

    if "min_risk" in filters:
        sql += " AND f.risk_score >= ?"
        params.append(float(filters["min_risk"]))

    if "max_risk" in filters:
        sql += " AND f.risk_score <= ?"
        params.append(float(filters["max_risk"]))

    if "min_anomaly" in filters:
        sql += " AND f.anomaly_score >= ?"
        params.append(float(filters["min_anomaly"]))

    if "max_anomaly" in filters:
        sql += " AND f.anomaly_score <= ?"
        params.append(float(filters["max_anomaly"]))

    if "search" in filters:
        sql += " AND e.router_hash LIKE ?"
        params.append(f"%{filters['search']}%")

    # ============================
    # COUNT FOR PAGINATION
    # ============================
    count_sql = "SELECT COUNT(*) FROM (" + sql + ")"
    total_items = q(count_sql, params)[0][0]

    page = int(request.args.get("page", 1))
    page, total_pages, offset, per_page = paginate(total_items, page)

    sql += " ORDER BY f.risk_score DESC NULLS LAST LIMIT ? OFFSET ?"
    params.extend([per_page, offset])

    rows = q(sql, params)

    # ============================
    # BUILD ROUTER OBJECTS
    # ============================
    routers_list = []
    for row in rows:
        (router_hash, risk, susp, anomaly,
         cluster_id, ent, per, stab,
         slope, dist,
         asn, country, vmaj, vmin, vpatch) = row

        routers_list.append(Obj(
            router_hash=router_hash,
            risk_score=risk or 0.0,
            suspiciousness=susp or 0.0,
            anomaly_score=anomaly or 0.0,
            cluster_id=cluster_id,
            entropy=ent or 0.0,
            periodicity=per or 0.0,
            stability=stab or 0.0,
            uptime_slope=slope or 0.0,
            cluster_distance=dist or 0.0,
            asn=asn,
            country=country,
            version=f"{vmaj}.{vmin}.{vpatch}"
        ))

    # ============================
    # INTELLIGENCE SUMMARY
    # ============================
    intel = Obj(
        router_count=total_items,
        avg_risk=q("SELECT AVG(risk_score) FROM router_features")[0][0] or 0.0,
        avg_susp=q("SELECT AVG(suspiciousness) FROM router_features")[0][0] or 0.0,
        avg_anomaly=q("SELECT AVG(anomaly_score) FROM router_features")[0][0] or 0.0,
        avg_uptime_slope=q("SELECT AVG(uptime_hours) FROM router_features")[0][0] or 0.0,
        high_risk=q("SELECT COUNT(*) FROM router_features WHERE risk_score >= 0.45")[0][0],
        suspicious=q("SELECT COUNT(*) FROM router_features WHERE suspiciousness >= 0.30")[0][0],
        unstable=q("SELECT COUNT(*) FROM router_behavior WHERE entropy > 30")[0][0],
        top_asn=q("""
            SELECT asn FROM enriched_router_data
            GROUP BY asn ORDER BY COUNT(*) DESC LIMIT 1
        """)[0][0],
        top_country=q("""
            SELECT country_geo FROM enriched_router_data
            GROUP BY country_geo ORDER BY COUNT(*) DESC LIMIT 1
        """)[0][0],
        cluster_count=q("SELECT COUNT(DISTINCT cluster_id) FROM router_behavior")[0][0],
        behavior_anomalies=q("""
            SELECT COUNT(*) FROM router_behavior WHERE cluster_distance >= 1.0
        """)[0][0]
    )

    return render_template(
        "routers.html",
        routers=routers_list,
        filters_qs=filters_qs(filters),
        intel=intel,
        filters=filters,
        page=page,
        total_pages=total_pages
    )

# ============================================================
# ROUTER DETAIL PAGE — FULLY CORRECTED
# ============================================================
@bp.route("/router/<router_hash>")
def router_detail(router_hash):

    # --------------------------------------------------------
    # BASIC ROUTER METADATA
    # --------------------------------------------------------
    meta = q("""
        SELECT
            asn, as_org, country_geo,
            version_major, version_minor, version_patch,
            supports_ipv6, supports_ntcp2, supports_ssu2, timestamp,
            latitude, longitude
        FROM enriched_router_data
        WHERE router_hash = ?
        LIMIT 1
    """, [router_hash])

    if not meta:
        return f"Router {router_hash} not found.", 404

    (asn, as_org, country, vmaj, vmin, vpatch,
     ipv6, ntcp2, ssu2, last_seen, latitude, longitude) = meta[0]
    has_geo = latitude is not None and longitude is not None

    # --------------------------------------------------------
    # FEATURES (RISK, SUSPICIOUSNESS, ANOMALY, UPTIME)
    # --------------------------------------------------------
    feat = q("""
        SELECT
            risk_score,
            suspiciousness,
            anomaly_score,
            uptime_hours
        FROM router_features
        WHERE router_hash = ?
        LIMIT 1
    """, [router_hash])

    if feat:
        (risk_score, suspiciousness, anomaly_score, uptime_hours) = feat[0]
    else:
        risk_score = suspiciousness = anomaly_score = uptime_hours = 0.0

    # --------------------------------------------------------
    # BEHAVIOR METRICS
    # --------------------------------------------------------
    beh = q("""
        SELECT entropy, periodicity, stability, cluster_distance, cluster_id
        FROM router_behavior
        WHERE router_hash = ?
        LIMIT 1
    """, [router_hash])

    if beh:
        entropy, periodicity, stability, cluster_distance, cluster_id = beh[0]
        entropy = entropy or 0.0
        periodicity = periodicity or 0.0
        stability = stability or 0.0
        cluster_distance = cluster_distance or 0.0
    else:
        entropy = periodicity = stability = cluster_distance = 0.0
        cluster_id = None

    # --------------------------------------------------------
    # ASN HISTORY
    # --------------------------------------------------------
    asn_history = [
        # router_asn_map.asn is INTEGER while enriched_router_data.asn (which
        # every per-ASN chart/map filename is derived from) is DOUBLE -- format
        # consistently here so these links land on the canonical "20473.0" URL
        # instead of "20473" (asn_detail() normalizes defensively too, but the
        # link itself should already be correct).
        Obj(asn=(str(float(row[0])) if row[0] is not None else None), as_org=row[1], timestamp=row[2])
        for row in q("""
            SELECT asn, as_org, timestamp
            FROM router_asn_map
            WHERE router_hash = ?
            ORDER BY timestamp
        """, [router_hash])
    ]

    # --------------------------------------------------------
    # COUNTRY HISTORY
    # --------------------------------------------------------
    # router_location_history has no country_geo column (lat/lon/timestamp only);
    # router_asn_map carries country alongside asn per snapshot, so use that.
    country_history = [
        Obj(country=row[0], timestamp=row[1])
        for row in q("""
            SELECT country, timestamp
            FROM router_asn_map
            WHERE router_hash = ?
            ORDER BY timestamp
        """, [router_hash])
    ]

    # --------------------------------------------------------
    # MOVEMENT HISTORY (ASN + COUNTRY)
    # --------------------------------------------------------
    movement = [
        Obj(asn=row[0], country=row[1], timestamp=row[2])
        for row in q("""
            SELECT asn, country, timestamp
            FROM router_asn_map
            WHERE router_hash = ?
            ORDER BY timestamp
        """, [router_hash])
    ]

    # --------------------------------------------------------
    # RISK TIMELINE
    # --------------------------------------------------------
    # No per-router risk history table exists (router_features is a
    # single current snapshot per router, not a time series).
    risk_timeline = []

    # --------------------------------------------------------
    # ANOMALY TIMELINE
    # --------------------------------------------------------
    anomaly_timeline = [
        # rule-based anomalies store NULL score (see anomalies() for why) --
        # coalesce, since router_detail.html formats this with "%.4f" which
        # raises on None.
        Obj(score=row[0] or 0.0, reason=row[1], timestamp=row[2])
        for row in q("""
            SELECT anomaly_score, reason, ts
            FROM router_anomalies
            WHERE router_hash = ?
            ORDER BY ts
        """, [router_hash])
    ]

    # --------------------------------------------------------
    # CAPABILITIES
    # --------------------------------------------------------
    caps = Obj(
        ipv6=bool(ipv6),
        ntcp2=bool(ntcp2),
        ssu2=bool(ssu2),
        ntcp=False,
        ssu=False,
        peer_test=False,
        client=False,
        version_flag=False,
        bw_flag=None,
        bw_rate=None
    )

    # --------------------------------------------------------
    # RISK EXPLANATION (NEW)
    # --------------------------------------------------------
    if risk_score >= 0.45:
        risk_explanation = "High risk: strong behavioral or statistical deviation."
    elif risk_score >= 0.30:
        risk_explanation = "Moderate risk: some unusual characteristics detected."
    else:
        risk_explanation = "Low risk: router appears normal."

    # --------------------------------------------------------
    # MAP FILENAMES (must match visualizations/maps/map_generator.py)
    # --------------------------------------------------------
    maps = Obj(
        pin=f"/visualizations/maps/router_{router_hash}_pin_map.html",
        movement=f"/visualizations/maps/router_{router_hash}_movement_map.html",
        risk=f"/visualizations/maps/router_{router_hash}_risk_map.html",
        suspicious=f"/visualizations/maps/router_{router_hash}_suspicious_map.html",
        behavior=f"/visualizations/maps/router_{router_hash}_behavior_map.html",
        cluster_distance=f"/visualizations/maps/router_{router_hash}_cluster_distance_map.html"
    )

    # --------------------------------------------------------
    # BUILD INFO OBJECT
    # --------------------------------------------------------
    info = Obj(
        router_hash=router_hash,
        asn=asn,
        as_org=as_org,
        country=country,
        version=f"{vmaj}.{vmin}.{vpatch}",
        ipv6=ipv6,
        ntcp2=ntcp2,
        ssu2=ssu2,

        risk_score=risk_score,
        suspiciousness=suspiciousness,
        anomaly_score=anomaly_score,
        uptime_slope=uptime_hours,
        uptime_hours=uptime_hours,
        timestamp=last_seen,
        risk_explanation=risk_explanation,

        entropy=entropy,
        periodicity=periodicity,
        stability=stability,
        cluster_distance=cluster_distance,
        cluster_id=cluster_id
    )

    return render_template(
        "router_detail.html",
        info=info,
        caps=caps,
        asn_history=asn_history,
        country_history=country_history,
        movement=movement,
        risk_timeline=risk_timeline,
        anomaly_timeline=anomaly_timeline,
        maps=maps,
        has_geo=has_geo
    )

# ============================================================
# MODULE 4 — COUNTRIES + COUNTRY DETAIL
# ============================================================

# ============================================================
# COUNTRIES OVERVIEW PAGE — FULLY CORRECTED
# ============================================================
@bp.route("/countries")
def countries():
    allowed = ["min_routers", "max_routers", "search"]
    filters = extract_filters(request.args, allowed)

    # --------------------------------------------------------
    # BASE QUERY — COUNTRY COUNTS
    # --------------------------------------------------------
    sql = """
        SELECT country_geo, COUNT(*) AS c
        FROM enriched_router_data
        WHERE country_geo IS NOT NULL
    """
    params = []

    if "search" in filters:
        sql += " AND country_geo LIKE ?"
        params.append(f"%{filters['search']}%")

    sql += " GROUP BY country_geo HAVING 1=1"

    if "min_routers" in filters:
        sql += " AND COUNT(*) >= ?"
        params.append(int(filters["min_routers"]))

    if "max_routers" in filters:
        sql += " AND COUNT(*) <= ?"
        params.append(int(filters["max_routers"]))

    sql += " ORDER BY c DESC"

    rows_raw = q(sql, params)

    countries_list = [
        Obj(country=row[0], count=row[1])
        for row in rows_raw
    ]

    # --------------------------------------------------------
    # PAGINATION
    # --------------------------------------------------------
    page = int(request.args.get("page", 1))
    per_page = 50
    total_items = len(countries_list)
    total_pages = max(1, (total_items + per_page - 1) // per_page)

    start = (page - 1) * per_page
    end = start + per_page
    countries_page = countries_list[start:end]

    # --------------------------------------------------------
    # COUNTRY INTELLIGENCE SUMMARY (NEW PIPELINE)
    # --------------------------------------------------------
    total_routers = q("SELECT COUNT(*) FROM enriched_router_data")[0][0]

    avg_risk = q("SELECT AVG(risk_score) FROM router_features")[0][0] or 0.0
    avg_suspicious = q("SELECT AVG(suspiciousness) FROM router_features")[0][0] or 0.0
    avg_anomaly = q("SELECT AVG(anomaly_score) FROM router_features")[0][0] or 0.0
    avg_uptime_slope = q("SELECT AVG(uptime_hours) FROM router_features")[0][0] or 0.0

    high_risk_countries = q("""
        SELECT COUNT(DISTINCT e.country_geo)
        FROM enriched_router_data e
        JOIN router_features f USING (router_hash)
        WHERE f.risk_score >= 0.45
    """)[0][0]

    suspicious_countries = q("""
        SELECT COUNT(DISTINCT e.country_geo)
        FROM enriched_router_data e
        JOIN router_features f USING (router_hash)
        WHERE f.suspiciousness >= 0.30
    """)[0][0]

    anomaly_countries = q("""
        SELECT COUNT(DISTINCT e.country_geo)
        FROM enriched_router_data e
        JOIN router_features f USING (router_hash)
        WHERE f.anomaly_score <= -0.30
    """)[0][0]

    intel = Obj(
        total_countries=len(countries_list),
        total_routers=total_routers,
        avg_per_country=(total_routers / len(countries_list)) if countries_list else 0,
        top_country=countries_list[0].country if countries_list else "N/A",
        avg_risk=avg_risk,
        avg_suspicious=avg_suspicious,
        avg_anomaly=avg_anomaly,
        avg_uptime_slope=avg_uptime_slope,   # template expects this name
        high_risk_countries=high_risk_countries,
        suspicious_countries=suspicious_countries,
        anomaly_countries=anomaly_countries
    )

    return render_template(
        "countries.html",
        countries=countries_page,
        filters=filters,
        filters_qs=filters_qs(filters),
        intel=intel,
        page=page,
        total_pages=total_pages
    )

# ============================================================
# GEOGRAPHIC CORRELATIONS — country x version / capability
# ============================================================
@bp.route("/geo-correlation")
def geo_correlation():
    rows = q("""
        SELECT
            e.country_geo,
            COUNT(*) AS router_count,
            AVG(COALESCE(r.version_risk, 0)) AS avg_version_risk,
            100.0 * SUM(CASE WHEN e.is_floodfill_cap THEN 1 ELSE 0 END) / COUNT(*) AS pct_floodfill,
            SUM(CASE WHEN e.is_floodfill_cap THEN 1 ELSE 0 END) AS floodfill_count,
            100.0 * SUM(CASE WHEN e.supports_ipv6 THEN 1 ELSE 0 END) / COUNT(*) AS pct_ipv6,
            SUM(CASE WHEN e.supports_ipv6 THEN 1 ELSE 0 END) AS ipv6_count,
            AVG(COALESCE(f.risk_score, 0)) AS avg_risk_score,
            AVG(CASE
                WHEN e.bw_unlimited THEN 3
                WHEN e.bw_high THEN 2
                WHEN e.bw_mid THEN 1
                WHEN e.bw_low THEN 0
                ELSE NULL
            END) AS avg_bw_tier,
            AVG(f.uptime_hours) AS avg_uptime_hours,
            AVG(e.days_since_published) AS avg_days_since_published
        FROM enriched_router_data e
        LEFT JOIN router_risk_scores r ON r.router_hash = e.router_hash
        LEFT JOIN router_features f ON f.router_hash = e.router_hash
        WHERE e.country_geo IS NOT NULL
        GROUP BY e.country_geo
        HAVING COUNT(*) >= 5
        ORDER BY router_count DESC
    """)

    countries = []
    for r in rows:
        router_count = r[1]
        floodfill_lo, floodfill_hi = wilson_confidence_interval(r[4] or 0, router_count)
        ipv6_lo, ipv6_hi = wilson_confidence_interval(r[6] or 0, router_count)
        countries.append(Obj(
            country=r[0],
            router_count=router_count,
            avg_version_risk=r[2] or 0.0,
            pct_floodfill=r[3] or 0.0,
            pct_floodfill_ci=(floodfill_lo * 100.0, floodfill_hi * 100.0),
            pct_ipv6=r[5] or 0.0,
            pct_ipv6_ci=(ipv6_lo * 100.0, ipv6_hi * 100.0),
            avg_risk_score=r[7] or 0.0,
            avg_bw_tier=r[8] or 0.0,
            avg_uptime_hours=r[9] or 0.0,
            avg_days_since_published=r[10] or 0.0
        ))

    chi2_result = compute_country_version_chi2()
    floodfill_chi2_result = compute_country_floodfill_chi2()
    bandwidth_chi2_result = compute_country_bandwidth_chi2()
    geo_cluster_risk_test, geo_cluster_anomaly_test = compute_geo_cluster_behavior_tests()

    return render_template(
        "geo_correlation.html",
        countries=countries,
        chi2_result=chi2_result,
        floodfill_chi2_result=floodfill_chi2_result,
        bandwidth_chi2_result=bandwidth_chi2_result,
        geo_cluster_risk_test=geo_cluster_risk_test,
        geo_cluster_anomaly_test=geo_cluster_anomaly_test
    )


def wilson_confidence_interval(successes, n, z=1.96):
    """
    95% Wilson score confidence interval for a proportion. Used instead of
    the naive normal approximation because several countries in this
    dataset have small router counts (as low as 5), where a point estimate
    like "80% floodfill" can be a single router's difference from "60%" --
    the interval width makes that uncertainty visible instead of implying
    false precision.
    """
    if n == 0:
        return (0.0, 0.0)
    p_hat = successes / n
    denom = 1 + z**2 / n
    center = (p_hat + z**2 / (2 * n)) / denom
    margin = (z * math.sqrt(p_hat * (1 - p_hat) / n + z**2 / (4 * n**2))) / denom
    return (max(0.0, center - margin), min(1.0, center + margin))


# Three independent chi-square tests (version, floodfill, bandwidth) are run
# against the same country groupings on this page. Testing multiple
# hypotheses at the same nominal alpha=0.05 inflates the true family-wise
# false-positive rate above 5% -- Bonferroni correction divides alpha by
# the number of tests to keep the *family's* error rate at 5%.
N_COUNTRY_CHI2_TESTS = 3
BONFERRONI_ALPHA = 0.05 / N_COUNTRY_CHI2_TESTS


def _chi2_and_cramers_v(pivot):
    """
    Shared core for every "is X distributed independently of country"
    test on this page: chi-square test of independence, plus Cramer's V
    effect size. With n in the thousands, chi-square's p-value alone can't
    distinguish "significant and substantial" from "significant but
    trivial" -- almost any association reaches p < 0.05 at this sample
    size. V (0-1 scale, using Bergsma's bias correction) measures the
    actual strength of the association, independent of n.
    """
    from scipy.stats import chi2_contingency

    if pivot.shape[0] < 2 or pivot.shape[1] < 2:
        return None

    chi2, p_value, dof, _ = chi2_contingency(pivot.values)

    n = pivot.values.sum()
    r, k = pivot.shape
    phi2 = chi2 / n
    phi2_corrected = max(0.0, phi2 - ((k - 1) * (r - 1)) / (n - 1))
    r_corrected = r - ((r - 1) ** 2) / (n - 1)
    k_corrected = k - ((k - 1) ** 2) / (n - 1)
    min_dim_corrected = min(r_corrected - 1, k_corrected - 1)
    cramers_v = math.sqrt(phi2_corrected / min_dim_corrected) if min_dim_corrected > 0 else 0.0

    if cramers_v < 0.1:
        effect_size_label = "negligible"
    elif cramers_v < 0.3:
        effect_size_label = "small"
    elif cramers_v < 0.5:
        effect_size_label = "moderate"
    else:
        effect_size_label = "large"

    return Obj(
        chi2=chi2,
        p_value=p_value,
        dof=dof,
        significant=p_value < 0.05,
        bonferroni_alpha=BONFERRONI_ALPHA,
        significant_bonferroni=p_value < BONFERRONI_ALPHA,
        cramers_v=cramers_v,
        effect_size_label=effect_size_label,
        n_countries=pivot.shape[0],
        n_categories=pivot.shape[1]
    )


def compute_country_version_chi2():
    """
    Chi-square test of independence: is a router's version distributed
    independently of its country, or is there a real, statistically
    significant association? Descriptive bar charts alone can't answer
    that -- two distributions can *look* different by chance on a
    small-ish sample. Restricted to the top 10 countries by router count
    and only rows with a known version_patch, since chi-square needs
    reasonably populated cells to be meaningful.

    Uses version_patch, not version_minor -- every I2P release for years
    has been "0.9.x", so version_minor is constant (always 9) across the
    entire dataset and carries no information. version_patch (the "x" in
    "0.9.x") is where the actual version variance lives.
    """
    import pandas as pd

    rows = q("""
        SELECT country_geo, version_patch, COUNT(*) AS cnt
        FROM enriched_router_data
        WHERE country_geo IN (
            SELECT country_geo FROM enriched_router_data
            WHERE country_geo IS NOT NULL
            GROUP BY country_geo
            HAVING COUNT(*) >= 20
            ORDER BY COUNT(*) DESC
            LIMIT 10
        )
        AND version_patch IS NOT NULL
        GROUP BY country_geo, version_patch
    """)

    if not rows:
        return None

    df = pd.DataFrame(rows, columns=["country", "version_patch", "cnt"])
    pivot = df.pivot_table(index="country", columns="version_patch", values="cnt", fill_value=0)
    result = _chi2_and_cramers_v(pivot)
    if result:
        result.n_versions = result.n_categories  # template compatibility
    return result


def compute_country_floodfill_chi2():
    """Same test, applied to floodfill capability (binary: yes/no) instead
    of version -- is floodfill adoption distributed independently of
    country, or is there a real, significant regional pattern?"""
    import pandas as pd

    rows = q("""
        SELECT country_geo, is_floodfill_cap, COUNT(*) AS cnt
        FROM enriched_router_data
        WHERE country_geo IN (
            SELECT country_geo FROM enriched_router_data
            WHERE country_geo IS NOT NULL
            GROUP BY country_geo
            HAVING COUNT(*) >= 20
            ORDER BY COUNT(*) DESC
            LIMIT 10
        )
        GROUP BY country_geo, is_floodfill_cap
    """)

    if not rows:
        return None

    df = pd.DataFrame(rows, columns=["country", "is_floodfill", "cnt"])
    pivot = df.pivot_table(index="country", columns="is_floodfill", values="cnt", fill_value=0)
    return _chi2_and_cramers_v(pivot)


def compute_country_bandwidth_chi2():
    """Same test, applied to bandwidth tier (ordinal: low/mid/high/
    unlimited, bucketed by each router's highest advertised tier) instead
    of version -- is bandwidth class distributed independently of country?"""
    import pandas as pd

    rows = q("""
        SELECT
            country_geo,
            CASE
                WHEN bw_unlimited THEN 'unlimited'
                WHEN bw_high THEN 'high'
                WHEN bw_mid THEN 'mid'
                WHEN bw_low THEN 'low'
                ELSE 'unknown'
            END AS bw_tier,
            COUNT(*) AS cnt
        FROM enriched_router_data
        WHERE country_geo IN (
            SELECT country_geo FROM enriched_router_data
            WHERE country_geo IS NOT NULL
            GROUP BY country_geo
            HAVING COUNT(*) >= 20
            ORDER BY COUNT(*) DESC
            LIMIT 10
        )
        GROUP BY country_geo, bw_tier
    """)

    if not rows:
        return None

    df = pd.DataFrame(rows, columns=["country", "bw_tier", "cnt"])
    pivot = df.pivot_table(index="country", columns="bw_tier", values="cnt", fill_value=0)
    return _chi2_and_cramers_v(pivot)


def _kruskal_and_effect_size(groups, metric_label):
    """
    Kruskal-Wallis H-test: do these groups come from the same underlying
    distribution, or does group membership predict the value of the
    metric? Non-parametric (no normality assumption), appropriate here
    since risk/anomaly scores are typically skewed, not normally
    distributed. Effect size is epsilon-squared (H-based analogue of
    eta-squared for ANOVA): 0-1 scale, how much of the metric's variance
    is explained by cluster membership, independent of sample size.
    """
    from scipy.stats import kruskal

    if len(groups) < 2:
        return None

    h_stat, p_value = kruskal(*groups)
    n = sum(len(g) for g in groups)
    k = len(groups)
    epsilon_sq = (h_stat - k + 1) / (n - k) if n > k else 0.0
    epsilon_sq = max(0.0, epsilon_sq)

    if epsilon_sq < 0.01:
        effect_size_label = "negligible"
    elif epsilon_sq < 0.06:
        effect_size_label = "small"
    elif epsilon_sq < 0.14:
        effect_size_label = "moderate"
    else:
        effect_size_label = "large"

    return Obj(
        metric_label=metric_label,
        h_stat=h_stat,
        p_value=p_value,
        dof=k - 1,
        significant=p_value < 0.05,
        epsilon_sq=epsilon_sq,
        effect_size_label=effect_size_label,
        n_clusters=k,
        n_routers=n
    )


def compute_geo_cluster_behavior_tests():
    """
    Do geographically clustered routers (pure spatial DBSCAN, see
    geo_clustering.py) also look behaviorally similar -- similar risk
    score, similar anomaly score? This is the first analysis in the
    project that directly tests whether the geographic and behavioral
    halves relate to each other, rather than reporting them side by side.
    Restricted to geo clusters with >= 5 routers (excludes DBSCAN noise,
    id = -1) for group sizes large enough to be meaningful.
    """
    rows = q("""
        SELECT g.geo_cluster_id, f.risk_score, f.anomaly_score
        FROM router_geo_clusters g
        JOIN router_features f ON f.router_hash = g.router_hash
        WHERE g.geo_cluster_id != -1
        AND g.geo_cluster_id IN (
            SELECT geo_cluster_id FROM router_geo_clusters
            WHERE geo_cluster_id != -1
            GROUP BY geo_cluster_id
            HAVING COUNT(*) >= 5
        )
    """)

    if not rows:
        return None, None

    from collections import defaultdict
    risk_groups = defaultdict(list)
    anomaly_groups = defaultdict(list)
    for cluster_id, risk_score, anomaly_score in rows:
        if risk_score is not None:
            risk_groups[cluster_id].append(risk_score)
        if anomaly_score is not None:
            anomaly_groups[cluster_id].append(anomaly_score)

    risk_result = _kruskal_and_effect_size(
        [v for v in risk_groups.values() if len(v) >= 5], "risk score"
    )
    anomaly_result = _kruskal_and_effect_size(
        [v for v in anomaly_groups.values() if len(v) >= 5], "anomaly score"
    )
    return risk_result, anomaly_result


METRIC_CORRELATION_COLUMNS = [
    ("risk_score", "Risk Score"),
    ("anomaly_score", "Anomaly Score"),
    ("suspiciousness", "Suspiciousness"),
    ("entropy", "Entropy"),
    ("periodicity", "Periodicity"),
    ("stability", "Stability"),
    ("cluster_distance", "Cluster Distance"),
    ("uptime_hours", "Uptime (h)"),
]


def compute_metric_correlation_matrix():
    """
    This project computes 8 separate metrics per router (risk, anomaly,
    suspiciousness, entropy, periodicity, stability, cluster distance,
    uptime) and has, up to now, always presented them as though each
    carries independent information. Nothing has actually checked that --
    if two of these are highly correlated, they're not really two signals,
    they're one signal counted twice, and any claim resting on "N
    independent indicators agree" would be overstated. Pearson correlation
    on every pair, flagging |r| >= 0.7 as a real redundancy concern.
    """
    import pandas as pd

    cols = [c for c, _ in METRIC_CORRELATION_COLUMNS]
    rows = q(f"""
        SELECT {", ".join(cols)}
        FROM router_features f
        LEFT JOIN router_behavior b USING (router_hash)
    """)

    if not rows:
        return None

    df = pd.DataFrame(rows, columns=cols)
    corr = df.corr(method="pearson", min_periods=30)

    pairs = []
    labels = dict(METRIC_CORRELATION_COLUMNS)
    for i, col_a in enumerate(cols):
        for col_b in cols[i + 1:]:
            r = corr.loc[col_a, col_b]
            if pd.isna(r):
                continue
            pairs.append(Obj(
                metric_a=labels[col_a],
                metric_b=labels[col_b],
                r=r,
                flagged=abs(r) >= 0.7
            ))

    pairs.sort(key=lambda p: abs(p.r), reverse=True)

    return Obj(
        matrix=corr,
        columns=[labels[c] for c in cols],
        pairs=pairs,
        n_routers=len(df),
        n_flagged=sum(1 for p in pairs if p.flagged)
    )


# ============================================================
# REPORT SUMMARY — citation-ready snapshot of key findings
# ============================================================
@bp.route("/summary")
def summary():
    total_routers = q("SELECT COUNT(*) FROM enriched_router_data")[0][0]
    total_asns = q("SELECT COUNT(DISTINCT asn) FROM enriched_router_data WHERE asn IS NOT NULL")[0][0]
    total_countries = q("SELECT COUNT(DISTINCT country_geo) FROM enriched_router_data WHERE country_geo IS NOT NULL")[0][0]

    window = q("SELECT MIN(timestamp), MAX(timestamp) FROM router_snapshots")[0]
    first_seen, last_seen = window[0], window[1]
    collection_days = (last_seen - first_seen).total_seconds() / 86400.0 if first_seen and last_seen else 0.0

    top_countries = q("""
        SELECT country_geo, COUNT(*) AS c
        FROM enriched_router_data
        WHERE country_geo IS NOT NULL
        GROUP BY country_geo
        ORDER BY c DESC
        LIMIT 5
    """)

    chi2_result = compute_country_version_chi2()
    floodfill_chi2_result = compute_country_floodfill_chi2()
    bandwidth_chi2_result = compute_country_bandwidth_chi2()
    geo_cluster_risk_test, geo_cluster_anomaly_test = compute_geo_cluster_behavior_tests()

    geo_clusters = q("""
        SELECT COUNT(DISTINCT geo_cluster_id), SUM(CASE WHEN geo_cluster_id = -1 THEN 1 ELSE 0 END), COUNT(*)
        FROM router_geo_clusters WHERE geo_cluster_id != -1
    """)[0]
    n_geo_clusters = geo_clusters[0] or 0
    geo_total = q("SELECT COUNT(*) FROM router_geo_clusters")[0][0] or 1
    geo_noise = q("SELECT COUNT(*) FROM router_geo_clusters WHERE geo_cluster_id = -1")[0][0] or 0
    geo_noise_pct = 100.0 * geo_noise / geo_total

    sensitivity_rows = q("""
        SELECT radius_km, n_clusters, ari_vs_baseline
        FROM geo_clustering_sensitivity ORDER BY radius_km
    """)

    vantage_row = q("""
        WITH per_router AS (
            SELECT router_hash,
                MAX(CASE WHEN seen_by = 'java_i2p' THEN 1 ELSE 0 END) AS seen_java,
                MAX(CASE WHEN seen_by = 'i2pd' THEN 1 ELSE 0 END) AS seen_i2pd
            FROM router_snapshots WHERE seen_by IS NOT NULL GROUP BY router_hash
        )
        SELECT
            SUM(CASE WHEN seen_java = 1 AND seen_i2pd = 1 THEN 1 ELSE 0 END) AS both_count,
            COUNT(*) AS either_count
        FROM per_router
    """)[0]
    jaccard_pct = 100.0 * (vantage_row[0] or 0) / (vantage_row[1] or 1)

    top5_asn_count = q("""
        SELECT SUM(c) FROM (
            SELECT COUNT(*) AS c FROM enriched_router_data
            WHERE asn IS NOT NULL GROUP BY asn ORDER BY c DESC LIMIT 5
        )
    """)[0][0] or 0
    total_with_asn = q("SELECT COUNT(*) FROM enriched_router_data WHERE asn IS NOT NULL")[0][0] or 1
    top5_asn_concentration_pct = 100.0 * top5_asn_count / total_with_asn

    pct_floodfill = q("SELECT 100.0 * SUM(CASE WHEN is_floodfill_cap THEN 1 ELSE 0 END) / COUNT(*) FROM enriched_router_data")[0][0] or 0.0
    pct_ipv6 = q("SELECT 100.0 * SUM(CASE WHEN supports_ipv6 THEN 1 ELSE 0 END) / COUNT(*) FROM enriched_router_data")[0][0] or 0.0

    return render_template(
        "summary.html",
        total_routers=total_routers,
        total_asns=total_asns,
        total_countries=total_countries,
        first_seen=first_seen,
        last_seen=last_seen,
        collection_days=collection_days,
        top_countries=[Obj(country=r[0], count=r[1]) for r in top_countries],
        chi2_result=chi2_result,
        floodfill_chi2_result=floodfill_chi2_result,
        bandwidth_chi2_result=bandwidth_chi2_result,
        geo_cluster_risk_test=geo_cluster_risk_test,
        geo_cluster_anomaly_test=geo_cluster_anomaly_test,
        n_geo_clusters=n_geo_clusters,
        geo_noise_pct=geo_noise_pct,
        sensitivity=[Obj(radius_km=r[0], n_clusters=r[1], ari=r[2]) for r in sensitivity_rows],
        jaccard_pct=jaccard_pct,
        both_count=vantage_row[0] or 0,
        either_count=vantage_row[1] or 0,
        top5_asn_concentration_pct=top5_asn_concentration_pct,
        pct_floodfill=pct_floodfill,
        pct_ipv6=pct_ipv6
    )


# ============================================================
# DATA EXPORT — the "concise dataset" deliverable, as a downloadable CSV
# ============================================================
@bp.route("/export.csv")
def export_csv():
    import csv
    import io

    # Deliberately excludes raw IP addresses. Everything else here is
    # either already-aggregated/derived (ASN, coarse city-level lat/lon,
    # country) or a computed research output (risk/behavior scores) -- for
    # an anonymity-network study, publishing the raw IPs of observed
    # routers isn't necessary for any of the paper's actual claims and
    # sits uncomfortably against the spirit of what's being studied, even
    # though this data is technically public/observable by any I2P router.
    rows = q("""
        SELECT
            e.router_hash, e.asn, e.as_org, e.country_geo, e.latitude, e.longitude,
            e.version_raw, e.version_major, e.version_minor, e.version_patch,
            e.caps_raw, e.is_floodfill_cap, e.is_reachable, e.is_hidden,
            e.supports_ipv6, e.supports_ntcp2, e.supports_ntcp, e.supports_ssu2, e.supports_ssu,
            e.bw_low, e.bw_mid, e.bw_high, e.bw_unlimited,
            e.seen_by, e.implementation, e.days_since_published,
            f.risk_score, f.anomaly_score, f.suspiciousness, f.uptime_hours,
            b.cluster_id, b.cluster_distance, b.entropy, b.periodicity, b.stability,
            g.geo_cluster_id
        FROM enriched_router_data e
        LEFT JOIN router_features f ON f.router_hash = e.router_hash
        LEFT JOIN router_behavior b ON b.router_hash = e.router_hash
        LEFT JOIN router_geo_clusters g ON g.router_hash = e.router_hash
        ORDER BY e.router_hash
    """)

    columns = [
        "router_hash", "asn", "as_org", "country", "latitude", "longitude",
        "version_raw", "version_major", "version_minor", "version_patch",
        "capabilities_raw", "is_floodfill", "is_reachable", "is_hidden",
        "supports_ipv6", "supports_ntcp2", "supports_ntcp", "supports_ssu2", "supports_ssu",
        "bw_low", "bw_mid", "bw_high", "bw_unlimited",
        "seen_by_vantage_point", "known_implementation", "days_since_published",
        "risk_score", "anomaly_score", "suspiciousness", "uptime_hours",
        "behavioral_cluster_id", "behavioral_cluster_distance", "entropy", "periodicity", "stability",
        "geo_cluster_id"
    ]

    buffer = io.StringIO()
    writer = csv.writer(buffer)
    writer.writerow(columns)
    writer.writerows(rows)

    return Response(
        buffer.getvalue(),
        mimetype="text/csv",
        headers={"Content-Disposition": "attachment; filename=i2p_router_dataset.csv"}
    )


# ============================================================
# METHODOLOGY / DATA QUALITY — instrumentation validation record
# ============================================================
@bp.route("/methodology")
def methodology():
    sensitivity_rows = q("""
        SELECT radius_km, n_clusters, n_noise, noise_pct, ari_vs_baseline, router_count
        FROM geo_clustering_sensitivity
        ORDER BY radius_km
    """)
    sensitivity = [
        Obj(
            radius_km=r[0],
            n_clusters=r[1],
            n_noise=r[2],
            noise_pct=r[3],
            ari=r[4],
            router_count=r[5]
        )
        for r in sensitivity_rows
    ]

    # Static record of measurement defects found and fixed while building
    # this system -- these are historical facts about the instrumentation,
    # not something derivable by querying current data, so they're kept
    # here rather than computed.
    fixes = [
        Obj(
            title="Capability-letter parsing didn't match the I2P spec",
            detail="parse_caps() checked wrong letters against the authoritative "
                   "I2P NetDB spec (e.g. treated 'E' as an introducer flag when it "
                   "actually means high congestion; 'P' as peer-test support when "
                   "it's a bandwidth tier). Bandwidth bucketing also silently "
                   "dropped the K, P, and X tiers entirely. Verified against "
                   "https://i2p.net/en/docs/overview/network-database and fixed."
        ),
        Obj(
            title="IPv6 detection was hardcoded to always-False",
            detail="A key-name mismatch (code read parsed['ipv6'], but the parser "
                   "returned the field as 'supports_ipv6') meant every router showed "
                   "supports_ipv6=False regardless of its real IP, since day one. "
                   "Fixed; the field now shows a real, non-zero adoption rate."
        ),
        Obj(
            title="ASN resolution silently skipped IPv6-only routers",
            detail="Found the same IPv4-only address-extraction bug independently "
                   "reimplemented in three separate files (router_enrichment.py, "
                   "asn_intel/enrichment.py, router_asn_map_builder.py). Each one "
                   "silently dropped ASN/geo lookups for IPv6-only routers. Fixed "
                   "all three; ASN resolution rate improved measurably."
        ),
        Obj(
            title="Timeseries table silently duplicated rows",
            detail="timeseries_router_stats had no primary key and was written via "
                   "plain INSERT, so any router whose snapshot didn't change between "
                   "pipeline runs got a fresh duplicate row every cycle. Surfaced as "
                   "a visible artifact: the dashboard's router-count chart showing an "
                   "impossible spike to 19,008 routers in one minute, when only 2,960 "
                   "distinct routers actually existed for that bucket. Deduplicated "
                   "(452,156 rows down to the true 178,049) and added a primary key "
                   "plus INSERT OR IGNORE to prevent recurrence."
        ),
        Obj(
            title="Dashboard queries leaked DB connections",
            detail="The query helper opened a fresh DuckDB connection per call and "
                   "never closed it. Enough leaked over the dashboard's uptime to "
                   "permanently block every external writer (pipeline runs, manual "
                   "analysis scripts) with a file-lock conflict. Fixed with a "
                   "try/finally close."
        ),
        Obj(
            title="Churn event timestamps were silently shifted 4 hours earlier",
            detail="churn_builder.py's normalize_ts() called tz_localize('UTC') on "
                   "timestamps that were already local wall-clock time (every naive "
                   "TIMESTAMP column in this schema is local, not UTC). When those "
                   "mislabeled values were written back into a naive DuckDB column, "
                   "the local-timezone conversion subtracted 4 hours (EDT offset), "
                   "so every churn event was stored 4 hours before it actually "
                   "happened -- surfaced as router_churn showing events that appeared "
                   "to predate the fresh data wipe, even though the source data "
                   "(timeseries_router_stats) had zero rows before the wipe. "
                   "Confirmed by re-running the churn builder directly and comparing "
                   "its output against the raw source timestamps for the same router. "
                   "Current risk-score computations were unaffected (they only use "
                   "relative time deltas between events, which a uniform shift "
                   "preserves), but any future feature filtering churn by absolute "
                   "date would have silently dropped real data. Fixed by removing the "
                   "incorrect re-localization."
        ),
        Obj(
            title="A later pipeline step silently discarded the entropy/periodicity/"
                  "stability fix",
            detail="ml/enriched_behavior_engine.py computes genuine temporal "
                   "entropy/periodicity/stability from each router's real observation "
                   "history. rebuild_behavior.py runs later in the same pipeline and "
                   "used to fully drop and rebuild router_behavior for its own "
                   "clustering step, recomputing entropy/periodicity/stability from "
                   "scratch as STDDEV/AVG/COUNT of uptime_hours -- silently discarding "
                   "the real metrics on every single pipeline run, so the fix never "
                   "actually reached the live dashboard after a full run. Surfaced "
                   "while investigating why cluster 5 showed no anomaly/entropy/"
                   "periodicity/uptime data: periodicity is mathematically bounded to "
                   "(0, 1] by its real formula, but the live data showed values up to "
                   "10.4, proving the substitute formula was the one actually live. "
                   "Fixed by having rebuild_behavior.py preserve the values already "
                   "written by the Enriched Behavior Engine and only own cluster "
                   "assignment, not recompute the temporal metrics."
        ),
        Obj(
            title="Per-ASN \"behavior\" map duplicated the \"main\" map instead of "
                  "showing distinct information",
            detail="asn_{asn}_map.html and asn_{asn}_behavior_map.html both colored "
                   "routers by hex_for_cluster(cluster_id) -- identical coloring, "
                   "identical data, so the two maps were visually indistinguishable "
                   "despite being presented as separate views. The equivalent "
                   "per-cluster map (cluster_{id}_behavior_map.html) already colored "
                   "by cluster_distance correctly; the ASN version was the one "
                   "inconsistent copy. Fixed by coloring the ASN behavior map by "
                   "cluster_distance (how far each router sits from its own "
                   "behavioral cluster's centroid) instead, matching the pattern "
                   "already used everywhere else."
        ),
        Obj(
            title="Risk detail page showed wrong values under correct-looking labels, "
                  "and two unrelated metric systems silently shared field names",
            detail="The /risk detail and /risk list pages labeled a field \"Uptime "
                   "Anomaly\" but displayed raw uptime_hours, and labeled another "
                   "\"Bandwidth Anomaly\" but displayed the generic ML anomaly score -- "
                   "neither is what the label claims. The genuinely correct, "
                   "already-computed values (plus transport_risk, caps_risk, "
                   "threat_exposure, churn_risk, and a ready-made plain-English "
                   "risk_explanation field) sat unused in router_risk_scores the whole "
                   "time. Separately: router_risk_scores had its own entropy/"
                   "periodicity/stability/behavior_cluster fields (computed from IP/ASN/"
                   "caps/transport churn events) under the exact same names as "
                   "router_behavior's entropy/periodicity/stability/cluster_id "
                   "(computed from real observation timestamps, see fix #7) -- two "
                   "unrelated computations, wildly different values, identical field "
                   "names, zero indication anywhere that they meant different things. "
                   "Confirmed by direct comparison on one router: entropy 1.296 vs "
                   "0.393, periodicity 0.552 vs 0.999, stability 34.0 vs 0.6, cluster 7 "
                   "vs 0. Fixed by querying the correct fields for the mislabeled "
                   "display, surfacing risk_explanation, and renaming risk_engine.py's "
                   "output columns to churn_entropy/churn_periodicity/churn_stability/"
                   "risk_cluster -- a pure rename with zero effect on any computed "
                   "value, verified by reading how each field is used internally "
                   "before touching it."
        ),
    ]

    correlation = compute_metric_correlation_matrix()

    return render_template(
        "methodology.html",
        sensitivity=sensitivity,
        fixes=fixes,
        correlation=correlation
    )


# ============================================================
# ROUTER MOVEMENT — routers with the most location churn
# ============================================================
@bp.route("/movement")
def movement():
    try:
        rows = q("""
            SELECT router_hash, total_km, distinct_locations, observations, first_seen, last_seen
            FROM router_movement_stats
            ORDER BY total_km DESC
            LIMIT 50
        """)
    except Exception:
        rows = []

    movers = [
        Obj(
            router_hash=r[0],
            total_km=r[1] or 0.0,
            distinct_locations=r[2],
            observations=r[3],
            first_seen=r[4],
            last_seen=r[5]
        )
        for r in rows
    ]

    return render_template("movement.html", movers=movers)


# ============================================================
# VANTAGE POINT COMPARISON — java_i2p vs i2pd overlap
# ============================================================
@bp.route("/vantage-comparison")
def vantage_comparison():
    row = q("""
        WITH per_router AS (
            SELECT
                router_hash,
                MAX(CASE WHEN seen_by = 'java_i2p' THEN 1 ELSE 0 END) AS seen_java,
                MAX(CASE WHEN seen_by = 'i2pd' THEN 1 ELSE 0 END) AS seen_i2pd
            FROM router_snapshots
            WHERE seen_by IS NOT NULL
            GROUP BY router_hash
        )
        SELECT
            SUM(CASE WHEN seen_java = 1 AND seen_i2pd = 0 THEN 1 ELSE 0 END) AS java_only,
            SUM(CASE WHEN seen_java = 0 AND seen_i2pd = 1 THEN 1 ELSE 0 END) AS i2pd_only,
            SUM(CASE WHEN seen_java = 1 AND seen_i2pd = 1 THEN 1 ELSE 0 END) AS both,
            COUNT(*) AS total_either
        FROM per_router
    """)[0]

    java_only, i2pd_only, both, total_either = (v or 0 for v in row)
    jaccard = (both / total_either) if total_either else 0.0

    stats = Obj(
        java_only=java_only,
        i2pd_only=i2pd_only,
        both=both,
        total_either=total_either,
        jaccard_pct=jaccard * 100.0
    )

    return render_template("vantage_comparison.html", stats=stats)


# ============================================================
# COUNTRY DETAIL PAGE — FULLY CORRECTED
# ============================================================
@bp.route("/country/<country>")
def country_detail(country):

    # --------------------------------------------------------
    # ROUTER COUNT
    # --------------------------------------------------------
    router_count = q("""
        SELECT COUNT(*)
        FROM enriched_router_data
        WHERE country_geo = ?
    """, [country])[0][0]

    # --------------------------------------------------------
    # AVG RISK / SUSPICIOUS / ANOMALY / UPTIME (NEW PIPELINE)
    # --------------------------------------------------------
    avg_risk = q("""
        SELECT AVG(f.risk_score)
        FROM router_features f
        JOIN enriched_router_data e USING (router_hash)
        WHERE e.country_geo = ?
    """, [country])[0][0] or 0.0

    avg_suspicious = q("""
        SELECT AVG(f.suspiciousness)
        FROM router_features f
        JOIN enriched_router_data e USING (router_hash)
        WHERE e.country_geo = ?
    """, [country])[0][0] or 0.0

    avg_anomaly = q("""
        SELECT AVG(f.anomaly_score)
        FROM router_features f
        JOIN enriched_router_data e USING (router_hash)
        WHERE e.country_geo = ?
    """, [country])[0][0] or 0.0

    # Option A: uptime_slope = uptime_hours
    avg_uptime_slope = q("""
        SELECT AVG(f.uptime_hours)
        FROM router_features f
        JOIN enriched_router_data e USING (router_hash)
        WHERE e.country_geo = ?
    """, [country])[0][0] or 0.0

    # --------------------------------------------------------
    # TOP ASNs
    # --------------------------------------------------------
    top_asns = q("""
        SELECT asn, as_org, COUNT(*) AS c
        FROM enriched_router_data
        WHERE country_geo = ?
        GROUP BY asn, as_org
        ORDER BY c DESC
        LIMIT 20
    """, [country])

    # --------------------------------------------------------
    # TOP CLUSTERS
    # --------------------------------------------------------
    top_clusters = q("""
        SELECT b.cluster_id, COUNT(*) AS c
        FROM router_behavior b
        JOIN enriched_router_data e USING (router_hash)
        WHERE e.country_geo = ?
        GROUP BY b.cluster_id
        ORDER BY c DESC
        LIMIT 20
    """, [country])

    # --------------------------------------------------------
    # ROUTERS IN THIS COUNTRY (NEW PIPELINE)
    # --------------------------------------------------------
    router_rows = q("""
        SELECT 
            e.router_hash,
            f.risk_score,
            f.suspiciousness,
            f.anomaly_score,
            b.cluster_id,
            e.asn,
            e.as_org,
            b.entropy,
            b.periodicity,
            b.stability,
            f.uptime_hours,          -- mapped to uptime_slope
            b.cluster_distance,
            e.version_major, e.version_minor, e.version_patch
        FROM enriched_router_data e
        LEFT JOIN router_features f USING (router_hash)
        LEFT JOIN router_behavior b USING (router_hash)
        WHERE e.country_geo = ?
        ORDER BY f.risk_score DESC NULLS LAST
        LIMIT 500
    """, [country])

    routers = [
        Obj(
            router_hash=r[0],
            risk_score=r[1] or 0.0,
            suspiciousness=r[2] or 0.0,
            anomaly_score=r[3] or 0.0,
            cluster_id=r[4],
            asn=r[5],
            as_org=r[6],
            entropy=r[7] or 0.0,
            periodicity=r[8] or 0.0,
            stability=r[9] or 0.0,
            uptime_slope=r[10] or 0.0,      # template expects this name
            cluster_distance=r[11] or 0.0,
            version=f"{r[12]}.{r[13]}.{r[14]}"
        )
        for r in router_rows
    ]

    # --------------------------------------------------------
    # MAP FILENAMES
    # --------------------------------------------------------
    maps = Obj(
        density=f"/visualizations/maps/country_density_map.html",
        heat=f"/visualizations/maps/country_heatmap.html",
        filtered=f"/visualizations/maps/country_filtered_map.html",
        risk=f"/visualizations/maps/country_risk_map.html",
        anomaly=f"/visualizations/maps/country_anomaly_map.html",
        behavior=f"/visualizations/maps/country_behavior_map.html"
    )

    return render_template(
        "country_detail.html",
        country=country,
        router_count=router_count,
        avg_risk=avg_risk,
        avg_suspicious=avg_suspicious,
        avg_anomaly=avg_anomaly,
        avg_uptime_slope=avg_uptime_slope,
        top_asns=top_asns,
        top_clusters=top_clusters,
        routers=routers,
        maps=maps
    )

# ============================================================
# MODULE 5 — CLUSTERS + CLUSTER DETAIL
# ============================================================

# ============================================================
# CLUSTERS OVERVIEW PAGE — FULLY CORRECTED
# ============================================================

@bp.route("/clusters")
def clusters():
    # Filters
    allowed = ["min_size", "max_size", "search", "cluster"]
    filters = extract_filters(request.args, allowed)

    # --------------------------------------------------------
    # BASE QUERY (corrected)
    # --------------------------------------------------------
    sql = """
        SELECT cluster_id, COUNT(*) AS c
        FROM router_behavior
        WHERE cluster_id IS NOT NULL
    """
    params = []

    # Search by cluster ID
    if "search" in filters:
        sql += " AND CAST(cluster_id AS VARCHAR) LIKE ?"
        params.append(f"%{filters['search']}%")

    if "cluster" in filters:
        sql += " AND cluster_id = ?"
        params.append(filters["cluster"])

    sql += " GROUP BY cluster_id HAVING 1=1"

    # Size filters (correct HAVING usage)
    if "min_size" in filters:
        sql += " AND COUNT(*) >= ?"
        params.append(int(filters["min_size"]))

    if "max_size" in filters:
        sql += " AND COUNT(*) <= ?"
        params.append(int(filters["max_size"]))

    sql += " ORDER BY c DESC"

    rows_raw = q(sql, params)

    clusters_list = [
        Obj(cluster_id=row[0], count=row[1])
        for row in rows_raw
    ]

    # --------------------------------------------------------
    # PAGINATION
    # --------------------------------------------------------
    page = int(request.args.get("page", 1))
    per_page = 50
    total_items = len(clusters_list)
    total_pages = max(1, (total_items + per_page - 1) // per_page)

    start = (page - 1) * per_page
    end = start + per_page
    clusters_page = clusters_list[start:end]

    # --------------------------------------------------------
    # CLUSTER INTELLIGENCE SUMMARY
    # --------------------------------------------------------
    total_routers = q("SELECT COUNT(*) FROM enriched_router_data")[0][0]

    intel = Obj(
        total_clusters=len(clusters_list),
        total_routers=total_routers,
        avg_cluster_size=(total_routers / len(clusters_list)) if clusters_list else 0,
        largest_cluster=clusters_list[0].cluster_id if clusters_list else "N/A",
        avg_risk=q("SELECT AVG(risk_score) FROM router_features")[0][0] or 0.0,
        avg_susp=q("SELECT AVG(suspiciousness) FROM router_features")[0][0] or 0.0,
        avg_anomaly=q("SELECT AVG(anomaly_score) FROM router_features")[0][0] or 0.0,
        avg_uptime_slope=q("SELECT AVG(uptime_hours) FROM router_features")[0][0] or 0.0,
        behavior_outliers=q("""
            SELECT COUNT(*) FROM router_behavior
            WHERE cluster_distance >= 1.0
        """)[0][0]
    )

    return render_template(
        "clusters.html",
        clusters=clusters_page,
        filters=filters,
        filters_qs=filters_qs(filters),
        intel=intel,
        page=page,
        total_pages=total_pages
    )

# ============================================================
# CLUSTER DETAIL PAGE — FULLY CORRECTED
# ============================================================
@bp.route("/cluster/<cluster_id>")
def cluster_detail(cluster_id):

    # --------------------------------------------------------
    # CLUSTER SIZE
    # --------------------------------------------------------
    cluster_size = q("""
        SELECT COUNT(*)
        FROM router_behavior
        WHERE cluster_id = ?
    """, [cluster_id])[0][0]

    # --------------------------------------------------------
    # AVG RISK / SUSPICIOUS / ANOMALY / UPTIME (NEW PIPELINE)
    # --------------------------------------------------------
    avg_risk = q("""
        SELECT AVG(f.risk_score)
        FROM router_features f
        JOIN router_behavior b USING (router_hash)
        WHERE b.cluster_id = ?
    """, [cluster_id])[0][0] or 0.0

    avg_suspicious = q("""
        SELECT AVG(f.suspiciousness)
        FROM router_features f
        JOIN router_behavior b USING (router_hash)
        WHERE b.cluster_id = ?
    """, [cluster_id])[0][0] or 0.0

    avg_anomaly = q("""
        SELECT AVG(f.anomaly_score)
        FROM router_features f
        JOIN router_behavior b USING (router_hash)
        WHERE b.cluster_id = ?
    """, [cluster_id])[0][0] or 0.0

    # Option A: uptime_slope = uptime_hours
    avg_uptime_slope = q("""
        SELECT AVG(f.uptime_hours)
        FROM router_features f
        JOIN router_behavior b USING (router_hash)
        WHERE b.cluster_id = ?
    """, [cluster_id])[0][0] or 0.0

    # --------------------------------------------------------
    # BEHAVIOR AVERAGES
    # --------------------------------------------------------
    avg_entropy, avg_periodicity, avg_stability, avg_cluster_distance = [
        x or 0.0 for x in q("""
            SELECT AVG(entropy), AVG(periodicity), AVG(stability),
                   AVG(cluster_distance)
            FROM router_behavior
            WHERE cluster_id = ?
        """, [cluster_id])[0]
    ]

    # --------------------------------------------------------
    # TOP ASNs
    # --------------------------------------------------------
    top_asns = q("""
        SELECT e.asn, e.as_org, COUNT(*) AS c
        FROM enriched_router_data e
        JOIN router_behavior b USING (router_hash)
        WHERE b.cluster_id = ?
        GROUP BY e.asn, e.as_org
        ORDER BY c DESC
        LIMIT 20
    """, [cluster_id])

    # --------------------------------------------------------
    # TOP COUNTRIES
    # --------------------------------------------------------
    top_countries = q("""
        SELECT e.country_geo, COUNT(*) AS c
        FROM enriched_router_data e
        JOIN router_behavior b USING (router_hash)
        WHERE b.cluster_id = ?
        GROUP BY e.country_geo
        ORDER BY c DESC
        LIMIT 20
    """, [cluster_id])

    # --------------------------------------------------------
    # ROUTERS IN THIS CLUSTER (NEW PIPELINE)
    # --------------------------------------------------------
    router_rows = q("""
        SELECT 
            e.router_hash,
            f.risk_score,
            f.suspiciousness,
            f.anomaly_score,
            b.entropy,
            b.periodicity,
            b.stability,
            f.uptime_hours,          -- mapped to uptime_slope
            b.cluster_distance,
            e.asn,
            e.country_geo,
            e.version_major, e.version_minor, e.version_patch
        FROM router_behavior b
        JOIN enriched_router_data e USING (router_hash)
        LEFT JOIN router_features f USING (router_hash)
        WHERE b.cluster_id = ?
        ORDER BY f.risk_score DESC NULLS LAST
        LIMIT 500
    """, [cluster_id])

    routers = [
        Obj(
            router_hash=r[0],
            risk_score=r[1] or 0.0,
            suspiciousness=r[2] or 0.0,
            anomaly_score=r[3] or 0.0,
            entropy=r[4] or 0.0,
            periodicity=r[5] or 0.0,
            stability=r[6] or 0.0,
            uptime_slope=r[7] or 0.0,      # template expects this name
            cluster_distance=r[8] or 0.0,
            asn=r[9],
            country=r[10],
            version=f"{r[11]}.{r[12]}.{r[13]}"
        )
        for r in router_rows
    ]

    # --------------------------------------------------------
    # CLUSTER MEANING
    # --------------------------------------------------------
    if avg_cluster_distance >= 1.0:
        cluster_meaning = "This cluster contains routers with highly unusual behavioral deviation."
    else:
        cluster_meaning = "This cluster represents normal behavioral patterns."

    # --------------------------------------------------------
    # MAP FILENAMES
    # --------------------------------------------------------
    maps = Obj(
        main=f"/visualizations/maps/cluster_{cluster_id}_map.html",
        risk=f"/visualizations/maps/cluster_{cluster_id}_risk_map.html",
        anomaly=f"/visualizations/maps/cluster_{cluster_id}_anomaly_map.html",
        behavior=f"/visualizations/maps/cluster_{cluster_id}_behavior_map.html"
    )

    return render_template(
        "cluster_detail.html",
        cluster_id=cluster_id,
        router_count=cluster_size,
        avg_risk=avg_risk,
        avg_suspicious=avg_suspicious,
        avg_anomaly=avg_anomaly,
        avg_entropy=avg_entropy,
        avg_periodicity=avg_periodicity,
        avg_stability=avg_stability,
        avg_uptime_slope=avg_uptime_slope,   # template expects this
        avg_cluster_distance=avg_cluster_distance,
        top_asns=top_asns,
        top_countries=top_countries,
        routers=routers,
        cluster_meaning=cluster_meaning,
        maps=maps
    )

# ============================================================
# MODULE 6 — ASN INTELLIGENCE
# ============================================================

# ============================================================
# ASN INTELLIGENCE OVERVIEW — FULLY CORRECTED
# ============================================================
@bp.route("/asn")
def asn_intel():
    allowed = ["search", "min_routers", "max_routers"]
    filters = extract_filters(request.args, allowed)

    # --------------------------------------------------------
    # BASE QUERY — ASN COUNTS
    # --------------------------------------------------------
    sql = """
        SELECT asn, as_org, COUNT(*) AS c
        FROM enriched_router_data
        WHERE asn IS NOT NULL
    """
    params = []

    if "search" in filters:
        sql += " AND (CAST(asn AS VARCHAR) LIKE ? OR as_org LIKE ?)"
        params.append(f"%{filters['search']}%")
        params.append(f"%{filters['search']}%")

    sql += " GROUP BY asn, as_org HAVING 1=1"

    if "min_routers" in filters:
        sql += " AND COUNT(*) >= ?"
        params.append(int(filters["min_routers"]))

    if "max_routers" in filters:
        sql += " AND COUNT(*) <= ?"
        params.append(int(filters["max_routers"]))

    sql += " ORDER BY c DESC"

    rows_raw = q(sql, params)

    asn_list = [
        Obj(asn=row[0], as_org=row[1], count=row[2])
        for row in rows_raw
    ]

    # --------------------------------------------------------
    # PAGINATION
    # --------------------------------------------------------
    page = int(request.args.get("page", 1))
    per_page = 50
    total_items = len(asn_list)
    total_pages = max(1, (total_items + per_page - 1) // per_page)

    start = (page - 1) * per_page
    end = start + per_page
    asn_page = asn_list[start:end]

    # --------------------------------------------------------
    # TOP ASNs BY COUNT
    # --------------------------------------------------------
    top_asns = q("""
        SELECT asn, as_org, COUNT(*) AS c
        FROM enriched_router_data
        GROUP BY asn, as_org
        ORDER BY c DESC
        LIMIT 20
    """)

    # --------------------------------------------------------
    # TOP ASNs BY RISK (NEW PIPELINE)
    # --------------------------------------------------------
    risk = q("""
        SELECT e.asn, e.as_org, AVG(f.risk_score) AS avg_risk
        FROM enriched_router_data e
        JOIN router_features f USING (router_hash)
        GROUP BY e.asn, e.as_org
        ORDER BY avg_risk DESC
        LIMIT 20
    """)

    # --------------------------------------------------------
    # TOP ASNs BY ANOMALY (NEW PIPELINE)
    # --------------------------------------------------------
    anomalies = q("""
        SELECT e.asn, e.as_org, AVG(f.anomaly_score) AS avg_anom
        FROM enriched_router_data e
        JOIN router_features f USING (router_hash)
        GROUP BY e.asn, e.as_org
        ORDER BY avg_anom DESC
        LIMIT 20
    """)

    # --------------------------------------------------------
    # CLUSTER DISTRIBUTION ACROSS ASNs
    # --------------------------------------------------------
    clusters = q("""
        SELECT b.cluster_id, COUNT(DISTINCT e.asn) AS asn_count
        FROM router_behavior b
        JOIN enriched_router_data e USING (router_hash)
        GROUP BY b.cluster_id
        ORDER BY asn_count DESC
    """)

    # --------------------------------------------------------
    # INTELLIGENCE SUMMARY (NEW PIPELINE)
    # --------------------------------------------------------
    total_routers = q("SELECT COUNT(*) FROM enriched_router_data")[0][0]

    avg_risk_all = q("SELECT AVG(risk_score) FROM router_features")[0][0] or 0.0
    avg_susp_all = q("SELECT AVG(suspiciousness) FROM router_features")[0][0] or 0.0
    avg_anom_all = q("SELECT AVG(anomaly_score) FROM router_features")[0][0] or 0.0
    avg_uptime_all = q("SELECT AVG(uptime_hours) FROM router_features")[0][0] or 0.0

    high_risk_asns = q("""
        SELECT COUNT(DISTINCT e.asn)
        FROM enriched_router_data e
        JOIN router_features f USING (router_hash)
        WHERE f.risk_score >= 0.45
    """)[0][0]

    suspicious_asns = q("""
        SELECT COUNT(DISTINCT e.asn)
        FROM enriched_router_data e
        JOIN router_features f USING (router_hash)
        WHERE f.suspiciousness >= 0.30
    """)[0][0]

    anomaly_asns = q("""
        SELECT COUNT(DISTINCT e.asn)
        FROM enriched_router_data e
        JOIN router_features f USING (router_hash)
        WHERE f.anomaly_score <= -0.30
    """)[0][0]

    intel = Obj(
        total_asns=len(asn_list),
        total_routers=total_routers,
        avg_per_asn=(total_routers / len(asn_list)) if asn_list else 0,
        top_asn=asn_list[0].asn if asn_list else "N/A",
        avg_risk=avg_risk_all,
        avg_suspicious=avg_susp_all,
        avg_anomaly=avg_anom_all,
        avg_uptime_slope=avg_uptime_all,   # template expects this name
        high_risk_asns=high_risk_asns,
        suspicious_asns=suspicious_asns,
        anomaly_asns=anomaly_asns
    )

    # --------------------------------------------------------
    # MAP FILENAMES
    # --------------------------------------------------------
    maps = Obj(
        density="/visualizations/maps/asn_density_map.html",
        risk="/visualizations/maps/asn_risk_map.html",
        anomaly="/visualizations/maps/asn_anomaly_map.html",
        behavior="/visualizations/maps/asn_behavior_map.html"
    )

    return render_template(
        "asn.html",
        asns=asn_page,
        top_asns=top_asns,
        risk=risk,
        anomalies=anomalies,
        clusters=clusters,
        filters=filters,
        filters_qs=filters_qs(filters),
        intel=intel,
        maps=maps,
        page=page,
        total_pages=total_pages
    )

# ============================================================
# ASN DETAIL PAGE — FULLY CORRECTED
# ============================================================
@bp.route("/asn/<asn>")
def asn_detail(asn):

    # enriched_router_data.asn is DOUBLE (renders as "20473.0"), but
    # router_asn_map.asn is INTEGER (renders as "20473") -- both link here
    # via the same route. Every per-ASN chart/map filename is generated from
    # the DOUBLE-formatted value, so normalize to that form up front or an
    # INTEGER-styled URL 404s on every chart/map despite the summary/table
    # queries below still resolving correctly (DuckDB casts either string
    # fine against the DOUBLE column).
    try:
        asn = str(float(asn))
    except (TypeError, ValueError):
        pass

    # --------------------------------------------------------
    # ROUTER COUNT
    # --------------------------------------------------------
    router_count = q("""
        SELECT COUNT(*)
        FROM enriched_router_data
        WHERE asn = ?
    """, [asn])[0][0]

    # --------------------------------------------------------
    # ASN ORGANIZATION NAME
    # --------------------------------------------------------
    as_org_row = q("""
        SELECT as_org
        FROM enriched_router_data
        WHERE asn = ?
        LIMIT 1
    """, [asn])
    as_org = as_org_row[0][0] if as_org_row else "Unknown"

    # --------------------------------------------------------
    # AVG RISK / SUSPICIOUS / ANOMALY / UPTIME (NEW PIPELINE)
    # --------------------------------------------------------
    avg_risk = q("""
        SELECT AVG(f.risk_score)
        FROM router_features f
        JOIN enriched_router_data e USING (router_hash)
        WHERE e.asn = ?
    """, [asn])[0][0] or 0.0

    avg_suspicious = q("""
        SELECT AVG(f.suspiciousness)
        FROM router_features f
        JOIN enriched_router_data e USING (router_hash)
        WHERE e.asn = ?
    """, [asn])[0][0] or 0.0

    avg_anomaly = q("""
        SELECT AVG(f.anomaly_score)
        FROM router_features f
        JOIN enriched_router_data e USING (router_hash)
        WHERE e.asn = ?
    """, [asn])[0][0] or 0.0

    # Option A: uptime_slope = uptime_hours
    avg_uptime_slope = q("""
        SELECT AVG(f.uptime_hours)
        FROM router_features f
        JOIN enriched_router_data e USING (router_hash)
        WHERE e.asn = ?
    """, [asn])[0][0] or 0.0

    # --------------------------------------------------------
    # BEHAVIOR AVERAGES
    # --------------------------------------------------------
    avg_entropy, avg_periodicity, avg_stability, avg_cluster_distance = [
        x or 0.0 for x in q("""
            SELECT AVG(b.entropy), AVG(b.periodicity), AVG(b.stability),
                   AVG(b.cluster_distance)
            FROM router_behavior b
            JOIN enriched_router_data e USING (router_hash)
            WHERE e.asn = ?
        """, [asn])[0]
    ]

    # --------------------------------------------------------
    # TOP COUNTRY
    # --------------------------------------------------------
    top_country = q("""
        SELECT country_geo
        FROM enriched_router_data
        WHERE asn = ?
        GROUP BY country_geo
        ORDER BY COUNT(*) DESC
        LIMIT 1
    """, [asn])
    top_country = top_country[0][0] if top_country else "Unknown"

    # --------------------------------------------------------
    # TOP CLUSTER
    # --------------------------------------------------------
    top_cluster = q("""
        SELECT b.cluster_id
        FROM router_behavior b
        JOIN enriched_router_data e USING (router_hash)
        WHERE e.asn = ?
        GROUP BY b.cluster_id
        ORDER BY COUNT(*) DESC
        LIMIT 1
    """, [asn])
    top_cluster = top_cluster[0][0] if top_cluster else "Unknown"

    # --------------------------------------------------------
    # VERSION COUNT
    # --------------------------------------------------------
    version_count = q("""
        SELECT COUNT(DISTINCT version_major || '.' || version_minor || '.' || version_patch)
        FROM enriched_router_data
        WHERE asn = ?
    """, [asn])[0][0]

    # --------------------------------------------------------
    # BEHAVIORAL OUTLIERS
    # --------------------------------------------------------
    behavior_outliers = q("""
        SELECT COUNT(*)
        FROM router_behavior b
        JOIN enriched_router_data e USING (router_hash)
        WHERE e.asn = ? AND b.cluster_distance >= 1.0
    """, [asn])[0][0]

    # --------------------------------------------------------
    # ROUTERS IN THIS ASN (NEW PIPELINE)
    # --------------------------------------------------------
    router_rows = q("""
        SELECT 
            e.router_hash,
            f.risk_score,
            f.suspiciousness,
            f.anomaly_score,
            b.cluster_id,
            b.entropy,
            b.periodicity,
            b.stability,
            f.uptime_hours,          -- mapped to uptime_slope
            b.cluster_distance,
            e.country_geo,
            e.version_major, e.version_minor, e.version_patch
        FROM enriched_router_data e
        LEFT JOIN router_features f USING (router_hash)
        LEFT JOIN router_behavior b USING (router_hash)
        WHERE e.asn = ?
        ORDER BY f.risk_score DESC NULLS LAST
        LIMIT 500
    """, [asn])

    routers = [
        Obj(
            router_hash=r[0],
            risk_score=r[1] or 0.0,
            suspiciousness=r[2] or 0.0,
            anomaly_score=r[3] or 0.0,
            cluster_id=r[4],
            entropy=r[5] or 0.0,
            periodicity=r[6] or 0.0,
            stability=r[7] or 0.0,
            uptime_slope=r[8] or 0.0,      # template expects this name
            cluster_distance=r[9] or 0.0,
            country=r[10],
            version=f"{r[11]}.{r[12]}.{r[13]}"
        )
        for r in router_rows
    ]

    # --------------------------------------------------------
    # MAP FILENAMES
    # --------------------------------------------------------
    maps = Obj(
        main=f"/visualizations/maps/asn_{asn}_map.html",
        risk=f"/visualizations/maps/asn_{asn}_risk_map.html",
        anomaly=f"/visualizations/maps/asn_{asn}_anomaly_map.html",
        behavior=f"/visualizations/maps/asn_{asn}_behavior_map.html"
    )

    has_geo = q("""
        SELECT COUNT(*)
        FROM enriched_router_data
        WHERE asn = ? AND latitude IS NOT NULL AND longitude IS NOT NULL
    """, [asn])[0][0] > 0

    return render_template(
        "asn_detail.html",
        asn=asn,
        has_geo=has_geo,
        as_org=as_org,
        router_count=router_count,
        avg_risk=avg_risk,
        avg_suspicious=avg_suspicious,
        avg_anomaly=avg_anomaly,
        avg_entropy=avg_entropy,
        avg_periodicity=avg_periodicity,
        avg_stability=avg_stability,
        avg_uptime_slope=avg_uptime_slope,
        avg_cluster_distance=avg_cluster_distance,
        top_country=top_country,
        top_cluster=top_cluster,
        version_count=version_count,
        behavior_outliers=behavior_outliers,
        top_countries=q("""
            SELECT country_geo, COUNT(*) AS c
            FROM enriched_router_data
            WHERE asn = ?
            GROUP BY country_geo
            ORDER BY c DESC
            LIMIT 20
        """, [asn]),
        top_clusters=q("""
            SELECT b.cluster_id, COUNT(*) AS c
            FROM router_behavior b
            JOIN enriched_router_data e USING (router_hash)
            WHERE e.asn = ?
            GROUP BY b.cluster_id
            ORDER BY c DESC
            LIMIT 20
        """, [asn]),
        routers=routers,
        maps=maps
    )

# ============================================================
# MODULE 7 — RISK, ANOMALIES, SUSPICIOUS, BEHAVIOR
# ============================================================

# ============================================================
# RISK OVERVIEW PAGE — FULLY CORRECTED
# ============================================================
@bp.route("/risk")
def risk():

    allowed = ["router", "asn", "country", "min_risk", "max_risk"]
    filters = extract_filters(request.args, allowed)

    # --------------------------------------------------------
    # BASE QUERY (NEW PIPELINE)
    # --------------------------------------------------------
    # country_risk_scores.country mixes 2-letter and 3-letter codes, so only
    # ~61 of 113 observed country_geo values match directly -- COALESCE to 0.0
    # for the rest rather than dropping those rows.
    sql = """
        SELECT
            f.router_hash,
            f.risk_score,
            COALESCE(vr.uptime_anomaly, 0.0) AS uptime_anomaly,
            COALESCE(vr.bandwidth_anomaly, 0.0) AS bandwidth_anomaly,
            COALESCE(vr.version_risk, 0.0) AS version_risk,
            b.cluster_distance AS cluster_outlier,
            COALESCE(cr.risk_score, 0.0) AS country_risk,
            COALESCE(ar.risk_score, 0.0) AS asn_risk,
            f.suspiciousness,
            e.asn,
            e.country_geo,
            b.cluster_id
        FROM router_features f
        JOIN enriched_router_data e USING (router_hash)
        LEFT JOIN router_behavior b USING (router_hash)
        LEFT JOIN country_risk_scores cr ON cr.country = e.country_geo
        LEFT JOIN asn_info ar ON ar.asn = CAST(e.asn AS INTEGER)
        LEFT JOIN router_risk_scores vr ON vr.router_hash = f.router_hash
        WHERE 1=1
    """
    params = []

    # --------------------------------------------------------
    # FILTERS
    # --------------------------------------------------------
    if "router" in filters:
        sql += " AND f.router_hash LIKE ?"
        params.append(f"%{filters['router']}%")

    if "asn" in filters:
        sql += " AND e.asn LIKE ?"
        params.append(f"%{filters['asn']}%")

    if "country" in filters:
        sql += " AND e.country_geo LIKE ?"
        params.append(f"%{filters['country']}%")

    if "min_risk" in filters:
        sql += " AND f.risk_score >= ?"
        params.append(float(filters["min_risk"]))

    if "max_risk" in filters:
        sql += " AND f.risk_score <= ?"
        params.append(float(filters["max_risk"]))

    sql += " ORDER BY f.risk_score DESC"

    rows_raw = q(sql, params)

    # --------------------------------------------------------
    # BUILD RISK OBJECTS
    # --------------------------------------------------------
    risks_list = [
        Obj(
            router_hash=r[0],
            risk_score=r[1],
            uptime_anomaly=r[2],
            bandwidth_anomaly=r[3],
            version_risk=r[4],
            cluster_outlier=r[5],
            country_risk=r[6],
            asn_risk=r[7],
            suspiciousness=r[8] or 0.0,
            asn=r[9],
            country=r[10],
            behavior_cluster=r[11]
        )
        for r in rows_raw
    ]

    # --------------------------------------------------------
    # PAGINATION
    # --------------------------------------------------------
    page = int(request.args.get("page", 1))
    per_page = 50
    total_items = len(risks_list)
    total_pages = max(1, (total_items + per_page - 1) // per_page)

    start = (page - 1) * per_page
    end = start + per_page
    risks_page = risks_list[start:end]

    # --------------------------------------------------------
    # INTELLIGENCE SUMMARY
    # --------------------------------------------------------
    intel = Obj(
        total_routers=len(risks_list),
        high_risk=sum(1 for r in risks_list if r.risk_score >= 0.45),
        avg_risk=(sum(r.risk_score for r in risks_list) / len(risks_list)) if risks_list else 0,
        avg_suspicious=(sum(r.suspiciousness for r in risks_list) / len(risks_list)) if risks_list else 0,
        top_asn=max(
            set(r.asn for r in risks_list if r.asn),
            key=lambda a: sum(1 for r in risks_list if r.asn == a)
        ) if risks_list else "N/A",
        top_country=max(
            set(r.country for r in risks_list if r.country),
            key=lambda c: sum(1 for r in risks_list if r.country == c)
        ) if risks_list else "N/A",
        top_cluster=max(
            set(r.behavior_cluster for r in risks_list if r.behavior_cluster),
            key=lambda cl: sum(1 for r in risks_list if r.behavior_cluster == cl)
        ) if risks_list else "N/A",
        behavior_outliers=sum(1 for r in risks_list if r.cluster_outlier >= 1.0)
    )

    # --------------------------------------------------------
    # MAP FILENAMES
    # --------------------------------------------------------
    maps = Obj(
        heat="/visualizations/maps/router_risk_heatmap.html",
        main="/visualizations/maps/router_risk_map.html",
        anomaly="/visualizations/maps/router_risk_anomaly_map.html",
        behavior="/visualizations/maps/router_risk_behavior_map.html"
    )

    return render_template(
        "risk.html",
        risks=risks_page,
        filters=filters,
        filters_qs=filters_qs(filters),
        intel=intel,
        maps=maps,
        page=page,
        total_pages=total_pages
    )

# ============================================================
# RISK DETAIL PAGE — FULLY CORRECTED
# ============================================================
@bp.route("/risk/<router_hash>")
def risk_detail(router_hash):

    # --------------------------------------------------------
    # BASIC ROUTER INFO
    # --------------------------------------------------------
    meta = q("""
        SELECT 
            asn, as_org, country_geo,
            version_major, version_minor, version_patch,
            supports_ipv6, supports_ntcp2, supports_ssu2
        FROM enriched_router_data
        WHERE router_hash = ?
        LIMIT 1
    """, [router_hash])

    if not meta:
        return f"Router {router_hash} not found.", 404

    (asn, as_org, country, vmaj, vmin, vpatch,
     ipv6, ntcp2, ssu2) = meta[0]

    # --------------------------------------------------------
    # RISK + SUSPICIOUSNESS + ANOMALY (NEW PIPELINE)
    # --------------------------------------------------------
    feat = q("""
        SELECT
            risk_score,
            suspiciousness,
            anomaly_score,
            uptime_hours
        FROM router_features
        WHERE router_hash = ?
        LIMIT 1
    """, [router_hash])

    if feat:
        (risk_score, suspiciousness, anomaly_score, uptime_hours) = feat[0]
    else:
        risk_score = suspiciousness = anomaly_score = uptime_hours = 0.0

    # --------------------------------------------------------
    # BEHAVIOR METRICS
    # --------------------------------------------------------
    beh = q("""
        SELECT entropy, periodicity, stability, cluster_distance, cluster_id
        FROM router_behavior
        WHERE router_hash = ?
        LIMIT 1
    """, [router_hash])

    if beh:
        entropy, periodicity, stability, cluster_distance, cluster_id = beh[0]
        entropy = entropy or 0.0
        periodicity = periodicity or 0.0
        stability = stability or 0.0
        cluster_distance = cluster_distance or 0.0
    else:
        entropy = periodicity = stability = cluster_distance = 0.0
        cluster_id = None

    # --------------------------------------------------------
    # FULL RISK BREAKDOWN (risk_engine.py's actual computed factors --
    # previously this page only pulled version_risk from this table and
    # substituted wrong proxies for uptime_anomaly/bandwidth_anomaly
    # (uptime_hours and the generic ML anomaly score, respectively,
    # neither of which is what those labels claim to show), while
    # transport_risk/caps_risk/threat_exposure/churn_risk/risk_explanation
    # were computed and stored but never displayed at all.
    # --------------------------------------------------------
    risk_row = q("""
        SELECT
            uptime_anomaly, bandwidth_anomaly, version_risk, cluster_outlier,
            transport_risk, caps_risk, threat_exposure, ml_anomaly, churn_risk,
            country_risk, asn_risk, uptime_slope,
            churn_stability, churn_periodicity, churn_entropy, risk_cluster,
            risk_explanation
        FROM router_risk_scores
        WHERE router_hash = ?
        LIMIT 1
    """, [router_hash])

    if risk_row:
        (uptime_anomaly, bandwidth_anomaly, version_risk, cluster_outlier,
         transport_risk, caps_risk, threat_exposure, ml_anomaly, churn_risk,
         country_risk, asn_risk, uptime_slope,
         churn_stability, churn_periodicity, churn_entropy, risk_cluster,
         risk_explanation) = risk_row[0]
    else:
        (uptime_anomaly, bandwidth_anomaly, version_risk, cluster_outlier,
         transport_risk, caps_risk, threat_exposure, ml_anomaly, churn_risk,
         country_risk, asn_risk, uptime_slope,
         churn_stability, churn_periodicity, churn_entropy) = [0.0] * 15
        risk_cluster = None
        risk_explanation = "No risk breakdown available yet for this router."

    # --------------------------------------------------------
    # ANOMALY TIMELINE
    # --------------------------------------------------------
    anomalies = [
        # rule-based anomalies store NULL score (see anomalies() for why) --
        # coalesce, since risk_detail.html formats this with "%.4f" which
        # raises on None.
        Obj(score=row[0] or 0.0, reason=row[1], timestamp=row[2])
        for row in q("""
            SELECT anomaly_score, reason, ts
            FROM router_anomalies
            WHERE router_hash = ?
            ORDER BY ts
        """, [router_hash])
    ]

    # --------------------------------------------------------
    # RISK TIMELINE
    # --------------------------------------------------------
    # No per-router risk history table exists (router_features is a
    # single current snapshot per router, not a time series).
    timeline = []

    # --------------------------------------------------------
    # MAP FILENAMES
    # --------------------------------------------------------
    maps = Obj(
        main=f"/visualizations/maps/router_{router_hash}_risk_map.html",
        heat=f"/visualizations/maps/router_{router_hash}_risk_heatmap.html",
        anomaly=f"/visualizations/maps/router_{router_hash}_risk_anomaly_map.html",
        behavior=f"/visualizations/maps/router_{router_hash}_risk_behavior_map.html"
    )

    # --------------------------------------------------------
    # BUILD INFO OBJECT
    # --------------------------------------------------------
    info = Obj(
        router_hash=router_hash,
        asn=asn,
        as_org=as_org,
        country=country,
        version=f"{vmaj}.{vmin}.{vpatch}",
        ipv6=ipv6,
        ntcp2=ntcp2,
        ssu2=ssu2,

        risk_score=risk_score,
        uptime_anomaly=uptime_anomaly or 0.0,
        bandwidth_anomaly=bandwidth_anomaly or 0.0,
        version_risk=version_risk or 0.0,
        cluster_outlier=cluster_outlier or 0.0,
        country_risk=country_risk or 0.0,
        asn_risk=asn_risk or 0.0,
        transport_risk=transport_risk or 0.0,
        caps_risk=caps_risk or 0.0,
        threat_exposure=threat_exposure or 0.0,
        ml_anomaly=ml_anomaly or 0.0,
        churn_risk=churn_risk or 0.0,
        uptime_slope=uptime_slope or 0.0,
        risk_explanation=risk_explanation,

        suspiciousness=suspiciousness,

        # From router_behavior (ml/enriched_behavior_engine.py) -- computed
        # from this router's real observation timestamps.
        entropy=entropy,
        periodicity=periodicity,
        stability=stability,
        cluster_distance=cluster_distance,
        cluster_id=cluster_id,

        # From router_risk_scores (risk_engine.py) -- computed from
        # IP/ASN/caps/transport churn events instead. Deliberately
        # separate names from the four fields above: these measure a
        # different thing from a different source, and silently sharing
        # the bare entropy/periodicity/stability/cluster names produced
        # two totally different numbers under identical labels.
        churn_entropy=churn_entropy,
        churn_periodicity=churn_periodicity,
        churn_stability=churn_stability,
        risk_cluster=risk_cluster,

        anomalies=anomalies
    )

    return render_template(
        "risk_detail.html",
        info=info,
        timeline=timeline,
        maps=maps
    )

# ============================================================
# ANOMALIES OVERVIEW PAGE — FULLY CORRECTED
# ============================================================
@bp.route("/anomalies")
def anomalies():

    allowed = ["router", "reason", "min_score", "max_score"]
    filters = extract_filters(request.args, allowed)

    # --------------------------------------------------------
    # BASE QUERY — NEW PIPELINE
    # --------------------------------------------------------
    sql = """
        SELECT 
            a.router_hash,
            a.anomaly_score,
            a.reason,
            a.ts,
            e.asn,
            e.country_geo,
            b.cluster_id
        FROM router_anomalies a
        LEFT JOIN enriched_router_data e USING (router_hash)
        LEFT JOIN router_behavior b USING (router_hash)
        WHERE 1=1
    """
    params = []

    # --------------------------------------------------------
    # FILTERS
    # --------------------------------------------------------
    if "router" in filters:
        sql += " AND a.router_hash LIKE ?"
        params.append(f"%{filters['router']}%")

    if "reason" in filters:
        sql += " AND a.reason LIKE ?"
        params.append(f"%{filters['reason']}%")

    if "min_score" in filters:
        sql += " AND a.anomaly_score >= ?"
        params.append(float(filters["min_score"]))

    if "max_score" in filters:
        sql += " AND a.anomaly_score <= ?"
        params.append(float(filters["max_score"]))

    sql += " ORDER BY a.anomaly_score DESC"

    rows_raw = q(sql, params)

    anomalies_list = [
        Obj(
            router_hash=r[0],
            # rule-based anomalies (ASN hopping / high churn) now store NULL
            # here instead of a fake -1.0 severity -- coalesce for the
            # summary stats below, same convention used everywhere else in
            # this file for nullable numeric columns.
            anomaly_score=r[1] or 0.0,
            reason=r[2],
            timestamp=r[3],
            asn=r[4],
            country=r[5],
            behavior_cluster=r[6]
        )
        for r in rows_raw
    ]

    # --------------------------------------------------------
    # PAGINATION
    # --------------------------------------------------------
    page = int(request.args.get("page", 1))
    per_page = 50
    total_items = len(anomalies_list)
    total_pages = max(1, (total_items + per_page - 1) // per_page)

    start = (page - 1) * per_page
    end = start + per_page
    anomalies_page = anomalies_list[start:end]

    # --------------------------------------------------------
    # INTELLIGENCE SUMMARY
    # --------------------------------------------------------
    intel = Obj(
        total_anomalies=len(anomalies_list),
        unique_routers=len(set(a.router_hash for a in anomalies_list)),
        avg_score=(sum(a.anomaly_score for a in anomalies_list) / len(anomalies_list)) if anomalies_list else 0,
        high_severity=sum(1 for a in anomalies_list if a.anomaly_score <= -0.30),
        top_reason=max(
            set(a.reason for a in anomalies_list),
            key=lambda r: sum(1 for a in anomalies_list if a.reason == r)
        ) if anomalies_list else "N/A",
        top_asn=max(
            set(a.asn for a in anomalies_list if a.asn),
            key=lambda asn: sum(1 for a in anomalies_list if a.asn == asn)
        ) if anomalies_list else "N/A",
        top_country=max(
            set(a.country for a in anomalies_list if a.country),
            key=lambda c: sum(1 for a in anomalies_list if a.country == c)
        ) if anomalies_list else "N/A",
        behavior_outliers=sum(1 for a in anomalies_list if a.behavior_cluster is not None)
    )

    return render_template(
        "anomalies.html",
        anomalies=anomalies_page,
        filters=filters,
        filters_qs=filters_qs(filters),
        intel=intel,
        page=page,
        total_pages=total_pages
    )

# ============================================================
# SUSPICIOUS OVERVIEW PAGE — FULLY CORRECTED
# ============================================================
@bp.route("/suspicious")
def suspicious():

    allowed = [
        "asn", "country", "cluster",
        "min_susp", "max_susp",
        "min_risk", "max_risk",
        "search"
    ]
    filters = extract_filters(request.args, allowed)

    # --------------------------------------------------------
    # BASE QUERY — NEW PIPELINE
    # --------------------------------------------------------
    sql = """
        SELECT 
            e.router_hash,
            f.suspiciousness,
            f.risk_score,
            b.cluster_id,
            b.entropy,
            b.periodicity,
            b.stability,
            f.uptime_hours,          -- mapped to uptime_slope
            e.asn,
            e.country_geo,
            e.version_major, e.version_minor, e.version_patch
        FROM router_features f
        JOIN enriched_router_data e USING (router_hash)
        LEFT JOIN router_behavior b USING (router_hash)
        WHERE f.suspiciousness >= 0.20
    """
    params = []

    # --------------------------------------------------------
    # FILTERS
    # --------------------------------------------------------
    if "asn" in filters:
        sql += " AND e.asn = ?"
        params.append(filters["asn"])

    if "country" in filters:
        sql += " AND e.country_geo = ?"
        params.append(filters["country"])

    if "cluster" in filters:
        sql += " AND b.cluster_id = ?"
        params.append(filters["cluster"])

    if "min_susp" in filters:
        sql += " AND f.suspiciousness >= ?"
        params.append(float(filters["min_susp"]))

    if "max_susp" in filters:
        sql += " AND f.suspiciousness <= ?"
        params.append(float(filters["max_susp"]))

    if "min_risk" in filters:
        sql += " AND f.risk_score >= ?"
        params.append(float(filters["min_risk"]))

    if "max_risk" in filters:
        sql += " AND f.risk_score <= ?"
        params.append(float(filters["max_risk"]))

    if "search" in filters:
        sql += " AND e.router_hash LIKE ?"
        params.append(f"%{filters['search']}%")

    # --------------------------------------------------------
    # COUNT FOR PAGINATION
    # --------------------------------------------------------
    count_sql = "SELECT COUNT(*) FROM (" + sql + ")"
    total_items = q(count_sql, params)[0][0]

    # --------------------------------------------------------
    # PAGINATION
    # --------------------------------------------------------
    page = int(request.args.get("page", 1))
    per_page = 50
    total_pages = max(1, (total_items + per_page - 1) // per_page)
    offset = (page - 1) * per_page

    sql += " ORDER BY f.suspiciousness DESC LIMIT ? OFFSET ?"
    params.extend([per_page, offset])

    rows = q(sql, params)

    routers = [
        Obj(
            router_hash=r[0],
            suspiciousness=r[1] or 0.0,
            risk_score=r[2] or 0.0,
            behavior_cluster=r[3],
            entropy=r[4] or 0.0,
            periodicity=r[5] or 0.0,
            stability=r[6] or 0.0,
            uptime_slope=r[7] or 0.0,      # template expects this name
            asn=r[8],
            country=r[9],
            version=f"{r[10]}.{r[11]}.{r[12]}"
        )
        for r in rows
    ]

    # --------------------------------------------------------
    # INTELLIGENCE SUMMARY (NEW PIPELINE)
    # --------------------------------------------------------
    avg_susp = q("SELECT AVG(suspiciousness) FROM router_features")[0][0] or 0.0
    avg_risk = q("SELECT AVG(risk_score) FROM router_features")[0][0] or 0.0

    intel = Obj(
        total_suspicious=total_items,
        avg_suspicious=avg_susp,
        avg_risk=avg_risk,
        high_risk=sum(1 for r in routers if r.risk_score >= 0.45),
        top_asn=q("""
            SELECT asn FROM enriched_router_data
            GROUP BY asn ORDER BY COUNT(*) DESC LIMIT 1
        """)[0][0],
        top_country=q("""
            SELECT country_geo FROM enriched_router_data
            GROUP BY country_geo ORDER BY COUNT(*) DESC LIMIT 1
        """)[0][0],
        cluster_count=q("SELECT COUNT(DISTINCT cluster_id) FROM router_behavior")[0][0],
        behavior_outliers=q("""
            SELECT COUNT(*) FROM router_behavior
            WHERE cluster_distance >= 1.0
        """)[0][0],
        version_count=q("""
            SELECT COUNT(DISTINCT version_major || '.' || version_minor || '.' || version_patch)
            FROM enriched_router_data
        """)[0][0]
    )

    return render_template(
        "suspicious.html",
        routers=routers,
        intel=intel,
        filters=filters,
        filters_qs=filters_qs(filters),
        page=page,
        total_pages=total_pages
    )

# ============================================================
# SUSPICIOUS DETAIL PAGE
# ============================================================
@bp.route("/suspicious/<router_hash>")
def suspicious_detail(router_hash):

    # --------------------------------------------------------
    # MAIN RECORD (NEW PIPELINE)
    # --------------------------------------------------------
    row = q("""
        SELECT 
            f.suspiciousness,
            f.risk_score,
            f.uptime_hours,          -- mapped to uptime_slope
            b.cluster_id,
            b.entropy, b.periodicity, b.stability, b.cluster_distance,
            e.asn, e.as_org, e.country_geo,
            e.version_major, e.version_minor, e.version_patch
        FROM router_features f
        JOIN enriched_router_data e USING (router_hash)
        LEFT JOIN router_behavior b USING (router_hash)
        WHERE f.router_hash = ?
        LIMIT 1
    """, [router_hash])

    if not row:
        return f"Router {router_hash} not found.", 404

    (susp, risk, uptime_hours, cluster,
     ent, per, stab, dist,
     asn, as_org, country,
     vmaj, vmin, vpatch) = row[0]

    info = Obj(
        router_hash=router_hash,
        suspiciousness=susp,
        risk_score=risk or 0.0,
        uptime_slope=uptime_hours or 0.0,   # template expects uptime_slope
        cluster_id=cluster,
        entropy=ent or 0.0,
        periodicity=per or 0.0,
        stability=stab or 0.0,
        cluster_distance=dist or 0.0,
        asn=asn,
        as_org=as_org,
        country=country,
        version=f"{vmaj}.{vmin}.{vpatch}"
    )

    return render_template(
        "suspicious_detail.html",
        info=info
    )

# ============================================================
# BEHAVIOR OVERVIEW PAGE — FULLY CORRECTED
# ============================================================
@bp.route("/behavior")
def behavior():
    allowed = [
        "router", "asn", "country", "cluster",
        "min_entropy", "max_entropy",
        "min_periodicity", "max_periodicity",
        "min_stability", "max_stability",
        "min_uptime", "max_uptime",
        "min_anomaly", "max_anomaly",
        "min_risk", "max_risk",
        "min_susp", "max_susp"
    ]
    filters = extract_filters(request.args, allowed)

    # --------------------------------------------------------
    # BASE QUERY — NEW PIPELINE
    # --------------------------------------------------------
    sql = """
        SELECT 
            e.router_hash,
            b.cluster_id,
            b.entropy,
            b.periodicity,
            b.stability,
            f.uptime_hours,          -- mapped to uptime_slope
            b.cluster_distance,
            f.anomaly_score,
            f.risk_score,
            f.suspiciousness,
            e.asn,
            e.country_geo
        FROM router_behavior b
        JOIN enriched_router_data e USING (router_hash)
        LEFT JOIN router_features f USING (router_hash)
        WHERE 1=1
    """
    params = []

    # --------------------------------------------------------
    # TEXT FILTERS
    # --------------------------------------------------------
    if "router" in filters:
        sql += " AND e.router_hash LIKE ?"
        params.append(f"%{filters['router']}%")

    if "asn" in filters:
        sql += " AND e.asn LIKE ?"
        params.append(f"%{filters['asn']}%")

    if "country" in filters:
        sql += " AND e.country_geo LIKE ?"
        params.append(f"%{filters['country']}%")

    if "cluster" in filters:
        sql += " AND b.cluster_id = ?"
        params.append(filters["cluster"])

    # --------------------------------------------------------
    # RANGE FILTERS
    # --------------------------------------------------------
    def add_range(field, min_key, max_key):
        nonlocal sql, params
        if min_key in filters:
            sql += f" AND {field} >= ?"
            params.append(float(filters[min_key]))
        if max_key in filters:
            sql += f" AND {field} <= ?"
            params.append(float(filters[max_key]))

    add_range("b.entropy", "min_entropy", "max_entropy")
    add_range("b.periodicity", "min_periodicity", "max_periodicity")
    add_range("b.stability", "min_stability", "max_stability")
    add_range("f.uptime_hours", "min_uptime", "max_uptime")
    add_range("f.anomaly_score", "min_anomaly", "max_anomaly")
    add_range("f.risk_score", "min_risk", "max_risk")
    add_range("f.suspiciousness", "min_susp", "max_susp")

    sql += " ORDER BY b.cluster_distance DESC"

    rows = q(sql, params)

    # --------------------------------------------------------
    # BUILD ROUTER OBJECTS
    # --------------------------------------------------------
    routers = [
        Obj(
            router_hash=r[0],
            cluster_id=r[1],
            # entropy/periodicity are genuinely NULL for routers with fewer
            # than 3 observations (not enough history for a meaningful
            # temporal metric yet) -- 0.0 here is a display fallback for
            # aggregation, not a claim that the router truly has zero
            # entropy/periodicity.
            entropy=r[2] or 0.0,
            periodicity=r[3] or 0.0,
            stability=r[4] or 0.0,
            uptime_slope=r[5] or 0.0,      # template expects this name
            cluster_distance=r[6] or 0.0,
            anomaly_score=r[7] or 0.0,
            risk_score=r[8] or 0.0,
            suspiciousness=r[9] or 0.0,
            asn=r[10],
            country=r[11]
        )
        for r in rows
    ]

    # --------------------------------------------------------
    # SUMMARY INTELLIGENCE
    # --------------------------------------------------------
    intel = Obj(
        total_routers=len(routers),
        outliers=sum(1 for r in routers if r.cluster_distance >= 1.0),
        avg_entropy=(sum(r.entropy for r in routers) / len(routers)) if routers else 0,
        avg_periodicity=(sum(r.periodicity for r in routers) / len(routers)) if routers else 0,
        avg_stability=(sum(r.stability for r in routers) / len(routers)) if routers else 0,
        avg_uptime_slope=(sum(r.uptime_slope for r in routers) / len(routers)) if routers else 0,
        avg_cluster_distance=(sum(r.cluster_distance for r in routers) / len(routers)) if routers else 0,
        avg_risk=(sum(r.risk_score for r in routers) / len(routers)) if routers else 0,
        avg_suspicious=(sum(r.suspiciousness for r in routers) / len(routers)) if routers else 0,
        top_cluster=max(
            set(r.cluster_id for r in routers),
            key=lambda c: sum(1 for r in routers if r.cluster_id == c)
        ) if routers else "N/A"
    )

    # --------------------------------------------------------
    # PAGINATION
    # --------------------------------------------------------
    page = int(request.args.get("page", 1))
    per_page = 50
    total_items = len(routers)
    total_pages = max(1, (total_items + per_page - 1) // per_page)

    start = (page - 1) * per_page
    end = start + per_page
    routers_page = routers[start:end]

    return render_template(
        "behavior.html",
        routers=routers_page,
        filters=filters,
        filters_qs=filters_qs(filters),
        intel=intel,
        page=page,
        total_pages=total_pages
    )

# ============================================================
# BEHAVIOR DETAIL PAGE — FULLY CORRECTED
# ============================================================
@bp.route("/behavior/<router_hash>")
def behavior_detail(router_hash):
    from visualizations.charts.behavior_charts import generate_router_behavior_charts
    generate_router_behavior_charts(router_hash)

    # --------------------------------------------------------
    # MAIN BEHAVIOR RECORD (NEW PIPELINE)
    # --------------------------------------------------------
    row = q("""
        SELECT 
            b.cluster_id,
            b.entropy,
            b.periodicity,
            b.stability,
            f.uptime_hours,          -- mapped to uptime_slope
            b.cluster_distance,
            f.anomaly_score,
            f.risk_score,
            f.suspiciousness
        FROM router_behavior b
        LEFT JOIN router_features f USING (router_hash)
        WHERE b.router_hash = ?
    """, [router_hash])

    if not row:
        return f"Router {router_hash} not found.", 404

    (cluster_id, ent, per, stab,
     uptime_hours, dist, anom, risk, susp) = row[0]

    # --------------------------------------------------------
    # BUILD BEHAVIOR OBJECT
    # --------------------------------------------------------
    behavior = Obj(
        cluster_id=cluster_id,
        entropy=ent or 0.0,
        periodicity=per or 0.0,
        stability=stab or 0.0,
        uptime_slope=uptime_hours or 0.0,   # template expects uptime_slope
        cluster_distance=dist or 0.0,
        anomaly_score=anom or 0.0,
        risk_score=risk or 0.0,
        suspiciousness=susp or 0.0,
        category="High Risk" if (dist or 0) >= 1.0 else "Normal",
        explanation=(
            "Router shows unusual behavioral deviation."
            if (dist or 0) >= 1.0
            else "Router behavior is within normal bounds."
        ),
        has_location=True
    )

    # --------------------------------------------------------
    # BEHAVIOR HISTORY (NEW PIPELINE)
    # --------------------------------------------------------
    # router_behavior_history has no uptime_slope/cluster_distance columns
    # (only router_hash, timestamp, cluster_id, entropy, periodicity, stability)
    history = [
        Obj(
            timestamp=h[0],
            entropy=h[1] or 0.0,
            periodicity=h[2] or 0.0,
            stability=h[3] or 0.0,
        )
        for h in q("""
            SELECT timestamp, entropy, periodicity, stability
            FROM router_behavior_history
            WHERE router_hash = ?
            ORDER BY timestamp
        """, [router_hash])
    ]

    # --------------------------------------------------------
    # MAP FILENAMES
    # --------------------------------------------------------
    maps = Obj(
        behavior=f"/visualizations/maps/router_{router_hash}_behavior_map.html",
        cluster_distance=f"/visualizations/maps/router_{router_hash}_cluster_distance_map.html",
        anomaly=f"/visualizations/maps/router_{router_hash}_anomaly_map.html",
        risk=f"/visualizations/maps/router_{router_hash}_risk_map.html",
        movement=f"/visualizations/maps/router_{router_hash}_movement_map.html",
        pin=f"/visualizations/maps/router_{router_hash}_pin_map.html"
    )

    return render_template(
        "behavior_detail.html",
        router_hash=router_hash,
        behavior=behavior,
        history=history,
        maps=maps
    )

# ============================================================
# MODULE 8 — MAPS + VISUALIZATION ROUTES (FULLY CORRECTED)
# ============================================================

from flask import send_from_directory
import os

# ------------------------------------------------------------
# Maps Index Page
# ------------------------------------------------------------
@bp.route("/maps")
def maps_index():

    # Global stats (corrected)
    global_stats = Obj(
        total_routers=q("SELECT COUNT(*) FROM enriched_router_data")[0][0],
        total_asns=q("SELECT COUNT(DISTINCT asn) FROM enriched_router_data")[0][0],
        total_countries=q("SELECT COUNT(DISTINCT country_geo) FROM enriched_router_data")[0][0],
        cluster_count=q("SELECT COUNT(DISTINCT cluster_id) FROM router_behavior")[0][0],
        avg_risk=q("SELECT AVG(risk_score) FROM router_features")[0][0] or 0.0,
        avg_suspicious=q("SELECT AVG(suspiciousness) FROM router_features")[0][0] or 0.0,
        high_risk=q("SELECT COUNT(*) FROM router_features WHERE risk_score >= 0.45")[0][0],
        suspicious=q("SELECT COUNT(*) FROM router_features WHERE suspiciousness >= 0.30")[0][0]
    )

    # Both lists below are paginated. Rendering them whole put 305k file links
    # and 29k router rows into one response -- a 61 MB page that locks up the
    # browser. They take separate page params so the two pagers on this page
    # don't fight over "page".
    mpage = int(request.args.get("mpage", 1))
    rpage = int(request.args.get("rpage", 1))

    # List all maps in the directory (cached; see list_map_files)
    all_maps = list_map_files()
    maps_total = len(all_maps)
    mpage, maps_total_pages, moffset, mper_page = paginate(maps_total, mpage, per_page=100)
    maps = all_maps[moffset:moffset + mper_page]

    # Router list for router‑level maps (only routers with coordinates get map files)
    routers_total = q("""
        SELECT COUNT(*) FROM enriched_router_data
        WHERE latitude IS NOT NULL AND longitude IS NOT NULL
    """)[0][0]
    rpage, routers_total_pages, roffset, rper_page = paginate(routers_total, rpage, per_page=100)
    router_list = [
        r[0] for r in q("""
            SELECT router_hash FROM enriched_router_data
            WHERE latitude IS NOT NULL AND longitude IS NOT NULL
            ORDER BY router_hash
            LIMIT ? OFFSET ?
        """, (rper_page, roffset))
    ]

    return render_template(
        "maps.html",
        maps=maps,
        global_stats=global_stats,
        router_list=router_list,
        mpage=mpage,
        maps_total_pages=maps_total_pages,
        maps_total=maps_total,
        rpage=rpage,
        routers_total_pages=routers_total_pages,
        routers_total=routers_total
    )

# ------------------------------------------------------------
# Serve any map by filename (generic loader)
# ------------------------------------------------------------
@bp.route("/maps/view/<filename>")
def map_view(filename):
    if not filename.endswith(".html"):
        filename += ".html"

    path = os.path.join(MAPS_PATH, filename)
    if not os.path.exists(path):
        return f"Map '{filename}' not found.", 404

    return send_from_directory(MAPS_PATH, filename)

# ------------------------------------------------------------
# Backwards‑compatible named routes
# ------------------------------------------------------------
@bp.route("/maps/global")
def map_global():
    return map_view("global_map.html")

@bp.route("/maps/routers")
def map_routers():
    return map_view("router_filtered_map.html")

@bp.route("/maps/clusters")
def map_clusters():
    return map_view("cluster_map.html")

@bp.route("/maps/asn")
def map_asn():
    return map_view("asn_risk_map.html")

# ------------------------------------------------------------
# Legacy fallback: /maps/<name>
# ------------------------------------------------------------
@bp.route("/maps/<name>")
def map_generic(name):
    filename = f"{name}.html"
    return map_view(filename)

