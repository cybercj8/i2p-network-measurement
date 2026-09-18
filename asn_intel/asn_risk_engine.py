import duckdb
import numpy as np
import pandas as pd

DB_PATH = "data/i2p.duckdb"

class UnifiedASNRiskEngine:

    def __init__(self):
        self.con = duckdb.connect(DB_PATH)

    # ---------------------------------------------------------
    # LOAD ASN + ROUTER DATA (CLEAN VERSION)
    # ---------------------------------------------------------
    def load_asn_data(self):
        return self.con.execute("""
            SELECT
                a.asn,
                a.as_org,
                a.country,
                a.reputation_tier,
                a.cluster_id,
                a.anomaly_score,

                COUNT(r.router_hash) AS router_count,
                AVG(r.risk_score) AS avg_router_risk,
                AVG(r.uptime_anomaly) AS avg_uptime_anomaly,
                AVG(r.bandwidth_anomaly) AS avg_bw_anomaly,
                AVG(r.version_risk) AS avg_version_risk,
                AVG(r.cluster_outlier) AS avg_cluster_outlier,
                AVG(r.country_risk) AS avg_country_risk

            FROM asn_info a
            LEFT JOIN router_asn_map m ON a.asn = m.asn
            LEFT JOIN router_risk_scores r ON m.router_hash = r.router_hash

            GROUP BY
                a.asn,
                a.as_org,
                a.country,
                a.reputation_tier,
                a.cluster_id,
                a.anomaly_score
        """).df()

    # ---------------------------------------------------------
    # COMPONENT SCORES
    # ---------------------------------------------------------
    def score_reputation(self, tier):
        mapping = {"A": 0.1, "B": 0.3, "C": 0.6, "D": 0.9}
        return mapping.get(tier, 0.5)

    def score_size(self, count):
        if count == 0:
            return 0.5
        return min(count / 5000, 1.0)

    def score_country(self, avg_country_risk):
        return float(avg_country_risk or 0.1)

    def score_router_risk(self, avg_router_risk):
        return float(avg_router_risk or 0.0)

    def score_anomaly(self, anomaly_scores):
        # anomaly_scores is a raw PCA distance-from-centroid (unbounded, currently
        # observed ~0.05-68.7), not a 0-1 probability like the other components --
        # weighting it in directly saturated ~5% of ASNs to a clipped risk_score of
        # exactly 1.0. Min-max scale it onto the same 0-1 footing as every other
        # component before it gets weighted into the composite sum below.
        filled = anomaly_scores.fillna(0.0)
        lo, hi = filled.min(), filled.max()
        if hi <= lo:
            return filled * 0.0
        return (filled - lo) / (hi - lo)

    # ---------------------------------------------------------
    # COMPOSITE ASN RISK
    # ---------------------------------------------------------
    def compute_asn_risk(self, df):
        rep = df["reputation_tier"].apply(self.score_reputation)
        size = df["router_count"].apply(self.score_size)
        router_risk = df["avg_router_risk"].apply(self.score_router_risk)
        anomaly = self.score_anomaly(df["anomaly_score"])
        country = df["avg_country_risk"].apply(self.score_country)

        risk = (
            0.30 * rep +
            0.20 * router_risk +
            0.20 * anomaly +
            0.15 * country +
            0.15 * size
        )

        return np.clip(risk, 0, 1)

    # ---------------------------------------------------------
    # WRITE RESULTS
    # ---------------------------------------------------------
    def write_results(self, df):
        for _, row in df.iterrows():
            self.con.execute("""
                UPDATE asn_info
                SET risk_score = ?
                WHERE asn = ?
            """, (float(row["asn_risk"]), int(row["asn"])))

        self.con.commit()

    # ---------------------------------------------------------
    # MAIN ENTRYPOINT
    # ---------------------------------------------------------
    def run(self):
        df = self.load_asn_data()

        if df.empty:
            print("[ASN RISK] No ASN data found.")
            return

        df["asn_risk"] = self.compute_asn_risk(df)

        self.write_results(df)
        print("[ASN RISK] ASN risk scores updated successfully.")

    def close(self):
        try:
            self.con.close()
        except:
            pass


if __name__ == "__main__":
    engine = UnifiedASNRiskEngine()
    engine.run()
    engine.close()

