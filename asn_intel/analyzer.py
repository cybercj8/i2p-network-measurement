import duckdb
import pandas as pd

DB_PATH = "data/i2p.duckdb"


class ASNAnalyzer:
    def __init__(self):
        self.con = duckdb.connect(DB_PATH)

    def load_router_data(self):
        """
        Load router-level enriched data created by router_enrichment.py.
        Updated to use country_geo + asn_country.
        """
        query = """
            SELECT
                router_hash,
                asn,
                as_org,
                asn_country,
                country_geo,
                supports_ipv6,
                supports_ntcp2,
                supports_ssu2,
                supports_ssu,
                router_age_days,
                caps_raw,
                version_raw
            FROM enriched_router_data
            WHERE asn IS NOT NULL
        """
        return self.con.execute(query).df()

    def compute_asn_stats(self, df):
        """
        Compute basic ASN-level statistics.
        """
        grouped = df.groupby("asn")

        stats = pd.DataFrame({
            "asn": grouped.size().index,
            "router_count": grouped.size().values,
            "floodfill_count": grouped["caps_raw"].apply(lambda x: x.str.count("f").sum()).values,
            "ipv6_count": grouped["supports_ipv6"].sum().values,
            "avg_router_age": (grouped["router_age_days"].mean().dt.total_seconds() / 86400.0).values
        })

        return stats

    def compute_transport_stats(self, df):
        """
        Compute transport protocol support counts per ASN.
        """
        rows = []

        for _, row in df.iterrows():
            rows.append((
                row["asn"],
                1 if row["supports_ntcp2"] else 0,
                1 if row["supports_ssu2"] else 0,
                1 if row["supports_ssu"] else 0
            ))

        tdf = pd.DataFrame(rows, columns=[
            "asn",
            "transport_ntcp2_count",
            "transport_ssu2_count",
            "transport_ssu_count"
        ])

        return tdf.groupby("asn").sum().reset_index()

    def compute_risk_score(self, stats):
        """
        Simple statistical risk model.
        """
        score = (
            (stats["floodfill_count"] / stats["router_count"]).fillna(0) * 0.4 +
            (stats["ipv6_count"] / stats["router_count"]).fillna(0) * 0.2 +
            (1 / (1 + stats["avg_router_age"])).fillna(0) * 0.4
        )
        return score

    def update_asn_info(self, df):
        """
        Merge analyzer results into the existing asn_info table.
        Updated to use asn_country + country_geo.
        """
        for _, row in df.iterrows():
            self.con.execute("""
                UPDATE asn_info
                SET
                    router_count = ?,
                    floodfill_count = ?,
                    ipv6_count = ?,
                    ntcp2_count = ?,
                    ssu2_count = ?,
                    ssu_count = ?,
                    avg_age = ?,
                    risk_score = COALESCE(risk_score, ?),
                    as_org = COALESCE(as_org, ?),
                    country = COALESCE(country, ?)
                WHERE asn = ?
            """, [
                int(row["router_count"]),
                int(row["floodfill_count"]),
                int(row["ipv6_count"]),
                int(row["transport_ntcp2_count"]),
                int(row["transport_ssu2_count"]),
                int(row["transport_ssu_count"]),
                float(row["avg_router_age"]),
                float(row["risk_score"]),
                row["as_org"],
                row["asn_country"],   # <-- updated
                int(row["asn"])
            ])

    def run(self):
        df = self.load_router_data()

        if df.empty:
            print("[ANALYZER] No router data found in enriched_router_data.")
            return

        stats = self.compute_asn_stats(df)
        transports = self.compute_transport_stats(df)

        merged = stats.merge(transports, on="asn", how="left")
        merged["risk_score"] = self.compute_risk_score(merged)

        # Add ASN name + country metadata
        meta = df.groupby("asn")[["as_org", "asn_country"]].first().reset_index()
        merged = merged.merge(meta, on="asn", how="left")

        self.update_asn_info(merged)

        print("[ANALYZER] ASN info updated successfully.")

    def close(self):
        self.con.close()


if __name__ == "__main__":
    analyzer = ASNAnalyzer()
    analyzer.run()
    analyzer.close()

