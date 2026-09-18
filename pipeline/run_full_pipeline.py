# ------------------------------------------------------------
# VISUALIZATION-ONLY PIPELINE (NEW)
# ------------------------------------------------------------

from pipeline.router_geo_builder import run_router_geo_builder
from visualizations.maps.map_generator import run_all_maps
from visualizations.charts.router_charts import run_router_charts
from visualizations.charts.cluster_charts import run_cluster_charts
from visualizations.charts.global_charts import run_global_charts
from scripts.generate_global_visuals import main as run_global_visuals
from ml.enriched_behavior_engine import run_enriched_behavior_engine

# Threat + country intel (still relevant)
from threat_intel.threat_exposure_engine import run_threat_exposure
from country_intel.country_risk_engine import run_country_risk


if __name__ == "__main__":
    print("[PIPELINE] Running threat exposure engine…")
    run_threat_exposure()

    print("[PIPELINE] Computing country risk scores…")
    run_country_risk()

    print("[PIPELINE] Rebuilding router geo tables…")
    run_router_geo_builder()
    
    print("[PIPELINE] Rebuilding enriched behavior…")
    run_enriched_behavior_engine()

    print("[PIPELINE] Rebuilding maps…")
    run_all_maps()

    print("[PIPELINE] Rebuilding router charts…")
    run_router_charts()

    print("[PIPELINE] Rebuilding cluster charts…")
    run_cluster_charts()

    print("[PIPELINE] Rebuilding global charts…")
    run_global_charts()

    print("[PIPELINE] Rebuilding global visuals…")
    run_global_visuals()

    print("[PIPELINE] Visualization pipeline complete.")

