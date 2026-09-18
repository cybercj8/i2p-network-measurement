import duckdb
import datetime


DB_PATH = "asn_intel.duckdb"


class TimeSeriesEngine:
    """
    Records periodic snapshots of ASN-level intelligence into
    timeseries_asn_stats. This preserves your existing logic but
    adds safety and consistency.
    """

    def __init__(self):
        self.con = duckdb.connect(DB_PATH)

    # ---------------------------------------------------------
    # Load ASN intelligence table
    # ---------------------------------------------------------
    def load_asn_info(self):
        df = self.con.execute("SELECT * FROM asn_info").df()
        return df

    # ---------------------------------------------------------
    # Write snapshot into timeseries table
    # ---------------------------------------------------------
    def write_snapshot(self, df):
        if df.empty:
            return

        timestamp = datetime.datetime.now(datetime.UTC).strftime("%Y-%m-%d %H:%M:%S")

        self.con.register("df_view", df)
        self.con.execute("""
            INSERT INTO timeseries_asn_stats
            SELECT
                ? AS timestamp,
                asn,
                router_count,
                floodfill_count,
                ipv6_count,
                risk_score,
                anomaly_score,
                cluster_id
            FROM df_view
        """, [timestamp])

    # ---------------------------------------------------------
    # MAIN EXECUTION
    # ---------------------------------------------------------
    def run(self):
        df = self.load_asn_info()
        self.write_snapshot(df)

    # ---------------------------------------------------------
    # Cleanup
    # ---------------------------------------------------------
    def close(self):
        try:
            self.con.close()
        except:
            pass

