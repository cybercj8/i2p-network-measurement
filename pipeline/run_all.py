import time
import traceback

# ------------------------------------------------------------
# HYBRID PIPELINE — NEW + OLD
# Verbose logging + human-friendly timing + hard-fail behavior
# ------------------------------------------------------------

# 1. Raw ingestion
from pipeline.router_snapshot_writer import run_snapshot_cycle
from pipeline.known_implementations import run_known_implementations

# 2. Timeseries + churn
from pipeline.router_timeseries_builder import run_router_timeseries
from asn_intel.churn_builder import ChurnBuilder

# 3. ASN map + enrichment
from pipeline.router_asn_map_builder import run_router_asn_map
from pipeline.router_enrichment import run_router_enrichment

# 4. ASN analytics
from asn_intel.enrichment import run_asn_enrichment
from asn_intel.analyzer import ASNAnalyzer
from asn_intel.clusters import ASNClusterEngine
from asn_intel.reputation import ASNReputationEngine

# 5. ML engines
from ml.router_anomaly_detection import run_router_anomaly_detection
from ml.enriched_behavior_engine import run_enriched_behavior_engine

# 6. Risk engines
from asn_intel.risk_engine import RouterRiskEngine
from asn_intel.asn_risk_engine import UnifiedASNRiskEngine

# 7. Geo history
from pipeline.router_geo_builder import run_router_geo_builder

# 8. Behavior rebuild (router_features, clustering, router_behavior, cluster_summary)
from rebuild_behavior import run_rebuild_behavior

# 8b. Pure geographic clustering (DBSCAN on lat/lon only -- separate from the
# mixed-feature behavioral KMeans clustering above)
from ml.geo_clustering import run_geo_clustering
from ml.geo_clustering_sensitivity import run_geo_clustering_sensitivity

# 9. Visualizations
from visualizations.maps.map_generator import run_all_maps
from visualizations.charts.router_charts import run_router_charts
from visualizations.charts.cluster_charts import run_cluster_charts
from visualizations.charts.global_charts import run_global_charts
from visualizations.charts.suspicious_charts import run_suspicious_charts
from visualizations.charts.behavior_charts import run_behavior_charts
from visualizations.charts.risk_charts import run_risk_charts
from visualizations.charts.country_charts import run_country_charts
from visualizations.charts.anomaly_charts import run_anomaly_charts
from visualizations.charts.asn_charts import run_asn_charts
from visualizations.charts.geo_correlation_charts import run_geo_correlation_charts
from visualizations.charts.vantage_charts import run_vantage_charts
from scripts.generate_global_visuals import main as run_global_visuals


# ------------------------------------------------------------
# Helper: human-friendly timing formatter
# ------------------------------------------------------------
def fmt_time(seconds):
    return f"{seconds:.2f}s ({time.strftime('%M:%S', time.gmtime(seconds))})"


# ------------------------------------------------------------
# Helper: run a pipeline step with logging + timing + hard-fail
# ------------------------------------------------------------
def run_step(name, func):
    print(f"\n[PIPELINE] Starting: {name}…")
    start = time.time()

    try:
        func()
    except Exception as e:
        print(f"[ERROR] Step '{name}' failed!")
        traceback.print_exc()
        raise e

    elapsed = time.time() - start
    print(f"[PIPELINE] Completed: {name} in {fmt_time(elapsed)}")


# ------------------------------------------------------------
# MAIN HYBRID PIPELINE
# ------------------------------------------------------------
if __name__ == "__main__":
    print("\n====================================================")
    print("        UNIFIED HYBRID PIPELINE — RUN ALL")
    print("====================================================\n")

    # 1. Raw ingestion
    run_step("Router Snapshot Writer", run_snapshot_cycle)
    # Must run before "Router Enrichment" below -- that step LEFT JOINs
    # known_implementations to tag enriched_router_data.implementation.
    run_step("Known Implementations (ground truth)", run_known_implementations)

    # 2. Timeseries + churn
    run_step("Router Timeseries Builder", run_router_timeseries)
    run_step("Churn Builder", lambda: ChurnBuilder().run())

    # 3. ASN map + enrichment
    run_step("Router ASN Map Builder", run_router_asn_map)
    run_step("Router Enrichment", run_router_enrichment)

    # 4. ASN analytics
    # Seeds/rebuilds asn_info from scratch. Must run before "ASN Analyzer" --
    # ASNAnalyzer.update_asn_info() only ever does UPDATE ... WHERE asn = ?,
    # it can't create rows, so without this step asn_info never gets
    # populated on a database that doesn't already have it from a prior run.
    run_step("ASN Enrichment (seed asn_info)", run_asn_enrichment)
    run_step("ASN Analyzer", lambda: ASNAnalyzer().run())
    run_step("ASN Clustering", lambda: ASNClusterEngine().run())
    # Must run before "ASN Risk Engine" below -- UnifiedASNRiskEngine reads
    # asn_info.anomaly_score as a 20%-weighted input to the composite risk score.
    run_step("ASN Reputation/Anomaly Scoring", lambda: ASNReputationEngine().run())

    # 5. ML engines
    # NOTE: "Router Suspicious Detection" (ml/router_suspicious_detection.py)
    # used to run here. Removed: its output table (router_suspicious) is
    # orphaned -- nothing downstream reads it, the real suspiciousness table
    # is `suspicious_routers` built by RouterRiskEngine below -- and it reads
    # `FROM router_risk_scores`, which RouterRiskEngine doesn't create until
    # the next stage, so on a fresh database this step crashed with
    # "Table with name router_risk_scores does not exist!" for no benefit.
    run_step("Router Anomaly Detection", run_router_anomaly_detection)
    run_step("Enriched Behavior Engine", run_enriched_behavior_engine)

    # 6. Risk engines
    run_step("Router Risk Engine", lambda: RouterRiskEngine().run())
    run_step("ASN Risk Engine", lambda: UnifiedASNRiskEngine().run())

    # 7. Geo history
    run_step("Router Geo Builder", run_router_geo_builder)

    # 8. Behavior rebuild (router_features, clustering, router_behavior, cluster_summary)
    # Supersedes the old "Cluster Summary Builder" step -- that script referenced
    # a nonexistent router_behavior.uptime_slope column and would have silently
    # dropped the cluster_summary this step builds without recreating it.
    run_step("Rebuild Behavior", run_rebuild_behavior)

    # 8b. Pure geographic clustering
    run_step("Geographic Clustering (DBSCAN)", run_geo_clustering)
    run_step("Geographic Clustering Sensitivity Analysis", run_geo_clustering_sensitivity)

    # 9. Visualizations
    run_step("Map Generator", run_all_maps)
    run_step("Router Charts", run_router_charts)
    run_step("Cluster Charts", run_cluster_charts)
    run_step("Global Charts", run_global_charts)
    run_step("Suspicious Charts", run_suspicious_charts)
    run_step("Behavior Charts", run_behavior_charts)
    run_step("Risk Charts", run_risk_charts)
    run_step("Country Charts", run_country_charts)
    run_step("Anomaly Charts", run_anomaly_charts)
    run_step("ASN Charts", run_asn_charts)
    run_step("Geo Correlation Charts", run_geo_correlation_charts)
    run_step("Vantage Point Charts", run_vantage_charts)
    run_step("Global Visuals", run_global_visuals)

    print("\n====================================================")
    print("        HYBRID PIPELINE COMPLETED SUCCESSFULLY")
    print("====================================================\n")


