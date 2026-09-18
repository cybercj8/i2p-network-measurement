import duckdb
import numpy as np
import pandas as pd
from sklearn.preprocessing import StandardScaler
from sklearn.decomposition import PCA
from sklearn.cluster import KMeans

DB_PATH = "data/i2p.duckdb"


class ASNReputationEngine:

    def __init__(self):
        self.con = duckdb.connect(DB_PATH)

    def load_asn_info(self):
        return self.con.execute("SELECT * FROM asn_info").df()

    def extract_features(self, df):
        # ASN-aggregate feature set -- must match asn_info's actual columns
        # (this previously listed per-router column names like
        # "transport_ntcp2_count"/"is_floodfill_cap"/"bw_low" that only exist
        # on enriched_router_data, not on this ASN-level table, so extract_features
        # raised a KeyError and the whole engine silently never ran).
        feature_cols = [
            "router_count",
            "floodfill_count",
            "ipv6_count",
            "ntcp2_count",
            "ssu2_count",
            "ssu_count",
            "avg_age",
            "risk_score",

            # Capability totals
            "total_floodfill",
            "total_reachable",
            "total_hidden",
            "total_unreachable",
            "total_congested_medium",
            "total_congested_high",
            "total_rejecting_tunnels",

            # Bandwidth totals
            "total_bw_low",
            "total_bw_mid",
            "total_bw_high",
            "total_bw_unlimited",

            # Transport totals
            "total_ssu",
            "total_ssu2",
            "total_ntcp",
            "total_ntcp2",
            "total_ipv6",

            # Version fields
            "version_major",
            "version_minor",
            "version_patch",
            "version_build",
        ]

        # Ensure all missing values are filled
        features = df[feature_cols].fillna(0)

        if len(features) == 0:
            return np.zeros((0, 2)), StandardScaler(), PCA(n_components=2)

        scaler = StandardScaler()
        scaled = scaler.fit_transform(features)

        pca = PCA(n_components=2)
        reduced = pca.fit_transform(scaled)

        return reduced, scaler, pca

    def compute_anomaly_scores(self, reduced):
        if len(reduced) == 0:
            return np.array([])

        centroid = np.mean(reduced, axis=0)
        return np.linalg.norm(reduced - centroid, axis=1)

    def assign_reputation(self, df, anomaly_scores):
        if len(df) == 0:
            df["reputation_tier"] = []
            return df

        percentiles = np.percentile(anomaly_scores, [25, 50, 75])

        tiers = []
        for score in anomaly_scores:
            if score <= percentiles[0]:
                tiers.append("A")
            elif score <= percentiles[1]:
                tiers.append("B")
            elif score <= percentiles[2]:
                tiers.append("C")
            else:
                tiers.append("D")

        df["reputation_tier"] = tiers
        df["anomaly_score"] = anomaly_scores
        return df

    def write_results(self, df):
        # Overwrite the entire table with updated reputation fields
        self.con.execute("DELETE FROM asn_info")
        self.con.register("df_view", df)

        # Insert all 38 columns in correct order
        self.con.execute("""
            INSERT INTO asn_info
            SELECT * FROM df_view
        """)

    def run(self):
        df = self.load_asn_info()

        reduced, scaler, pca = self.extract_features(df)
        anomaly_scores = self.compute_anomaly_scores(reduced)

        df = self.assign_reputation(df, anomaly_scores)
        self.write_results(df)

    def close(self):
        try:
            self.con.close()
        except:
            pass

