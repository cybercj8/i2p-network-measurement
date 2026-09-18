import duckdb
from asn_intel.extractor import ASNExtractor

DB_PATH = "asn_intel.duckdb"


def initialize_schema():
    con = duckdb.connect(DB_PATH)

    con.execute("""
        CREATE TABLE IF NOT EXISTS router_asn_map (
            router_hash TEXT,
            asn INTEGER,
            asn_confidence REAL,
            extracted_from TEXT
        )
    """)

    con.execute("""
        CREATE TABLE IF NOT EXISTS asn_info (
            asn INTEGER,
            asn_name TEXT,
            country TEXT,
            router_count INTEGER,
            floodfill_count INTEGER,
            ipv6_count INTEGER,
            transport_ntcp2_count INTEGER,
            transport_ssu2_count INTEGER,
            transport_ssu_count INTEGER,
            avg_router_age REAL,
            risk_score REAL,
            cluster_id INTEGER
        )
    """)

    con.execute("""
        CREATE TABLE IF NOT EXISTS timeseries_asn_stats (
            timestamp TEXT,
            asn INTEGER,
            router_count INTEGER,
            floodfill_count INTEGER,
            ipv6_count INTEGER,
            risk_score REAL,
            anomaly_score REAL,
            cluster_id INTEGER
        )
    """)

    con.close()


def run_extraction():
    initialize_schema()

    extractor = ASNExtractor()
    try:
        extractor.run()
    finally:
        extractor.close()


if __name__ == "__main__":
    run_extraction()

