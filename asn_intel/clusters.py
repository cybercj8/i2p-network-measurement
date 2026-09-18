import duckdb
import pandas as pd
from sklearn.preprocessing import StandardScaler
from sklearn.decomposition import PCA
from sklearn.cluster import KMeans

DB_PATH = "data/i2p.duckdb"


class ASNClusterEngine:
    def __init__(self, n_clusters=6):
        self.con = duckdb.connect(DB_PATH)
        self.n_clusters = n_clusters

    def load_asn_info(self):
        """
        Load ASN-level features AFTER enrichment + analyzer.
        Only include ASNs with enough data to cluster.
        """
        query = """
            SELECT
                asn,
                router_count,
                floodfill_count,
                ipv6_count,
                ntcp2_count,
                ssu2_count,
                ssu_count,
                avg_age,
                COALESCE(risk_score, 0) AS risk_score
            FROM asn_info
            WHERE router_count > 0
        """
        return self.con.execute(query).df()

    def extract_features(self, df):
        """
        Select meaningful features for clustering.
        Normalize them and reduce dimensionality with PCA.
        """
        features = df[[
            "router_count",
            "floodfill_count",
            "ipv6_count",
            "ntcp2_count",
            "ssu2_count",
            "ssu_count",
            "avg_age",
            "risk_score"
        ]].fillna(0)

        scaler = StandardScaler()
        scaled = scaler.fit_transform(features)

        pca = PCA(n_components=3)
        reduced = pca.fit_transform(scaled)

        return reduced

    def run_kmeans(self, reduced):
        """
        Run KMeans clustering on PCA-reduced features.
        """
        model = KMeans(n_clusters=self.n_clusters, n_init="auto")
        labels = model.fit_predict(reduced)
        return labels

    def update_clusters(self, df, labels):
        """
        Write cluster assignments back into asn_info.
        """
        for asn, label in zip(df["asn"], labels):
            self.con.execute("""
                UPDATE asn_info
                SET cluster_id = ?
                WHERE asn = ?
            """, [int(label), int(asn)])

    def run(self):
        df = self.load_asn_info()

        if df.empty:
            print("[WARN] No ASN data available for clustering.")
            return

        reduced = self.extract_features(df)
        labels = self.run_kmeans(reduced)
        self.update_clusters(df, labels)

        print(f"[CLUSTER] Assigned {self.n_clusters} clusters to {len(df)} ASNs.")

    def close(self):
        self.con.close()

