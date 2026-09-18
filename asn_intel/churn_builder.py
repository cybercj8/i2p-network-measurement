import duckdb
import pandas as pd

DB_PATH = "data/i2p.duckdb"


class ChurnBuilder:

    def __init__(self):
        self.con = duckdb.connect(DB_PATH)
        self.ensure_churn_table()

    # ---------------------------------------------------------
    # ENSURE TABLE EXISTS
    # ---------------------------------------------------------
    def ensure_churn_table(self):
        self.con.execute("""
            CREATE TABLE IF NOT EXISTS router_churn (
                router_hash TEXT,
                timestamp TIMESTAMP,
                event TEXT,
                old_ip TEXT,
                new_ip TEXT,
                old_asn INTEGER,
                new_asn INTEGER,
                old_caps TEXT,
                new_caps TEXT,
                old_transport TEXT,
                new_transport TEXT
            )
        """)

    # ---------------------------------------------------------
    # LOAD TIMESERIES DATA
    # ---------------------------------------------------------
    def load_timeseries(self):
        return self.con.execute("""
            SELECT
                router_hash,
                timestamp,
                ip,
                asn,
                caps,
                transport
            FROM timeseries_router_stats
            ORDER BY router_hash, timestamp
        """).df()

    # ---------------------------------------------------------
    # NORMALIZE TIMESTAMP
    # ---------------------------------------------------------
    # Every other table in this schema stores naive TIMESTAMP columns as
    # local wall-clock values (not UTC) -- timeseries_router_stats.timestamp
    # loaded via .df() already comes through as a naive local-time value.
    # Previously this called tz_localize("UTC"), which mislabeled that
    # local time as UTC; when DuckDB later converted it back to a naive
    # column for storage, it subtracted the local UTC offset (-4h EDT),
    # silently shifting every churn event 4 hours earlier than its true
    # source timestamp. Just pass the value through unchanged instead --
    # detect_churn() only needs a consistent, orderable timestamp per
    # event, not a UTC-normalized one.
    def normalize_ts(self, ts):
        if isinstance(ts, str):
            ts = pd.to_datetime(ts)
        return ts

    # ---------------------------------------------------------
    # DETECT CHURN EVENTS
    # ---------------------------------------------------------
    def detect_churn(self, df):
        events = []

        for router_hash, group in df.groupby("router_hash"):
            group = group.sort_values("timestamp")

            prev_ip = None
            prev_asn = None
            prev_caps = None
            prev_transport = None

            for _, row in group.iterrows():
                ts = self.normalize_ts(row["timestamp"])

                # -------------------------
                # IP CHANGE
                # -------------------------
                if (
                    prev_ip is not None
                    and pd.notna(row["ip"])
                    and row["ip"] != prev_ip
                ):
                    events.append({
                        "router_hash": router_hash,
                        "timestamp": ts,
                        "event": "ip_change",
                        "old_ip": prev_ip,
                        "new_ip": row["ip"],
                        "old_asn": None,
                        "new_asn": None,
                        "old_caps": None,
                        "new_caps": None,
                        "old_transport": None,
                        "new_transport": None
                    })

                # -------------------------
                # ASN CHANGE
                # -------------------------
                if (
                    prev_asn is not None
                    and pd.notna(row["asn"])
                    and row["asn"] != prev_asn
                ):
                    events.append({
                        "router_hash": router_hash,
                        "timestamp": ts,
                        "event": "asn_change",
                        "old_ip": None,
                        "new_ip": None,
                        "old_asn": prev_asn,
                        "new_asn": row["asn"],
                        "old_caps": None,
                        "new_caps": None,
                        "old_transport": None,
                        "new_transport": None
                    })

                # -------------------------
                # CAPS CHANGE
                # -------------------------
                if (
                    prev_caps is not None
                    and pd.notna(row["caps"])
                    and row["caps"] != prev_caps
                ):
                    events.append({
                        "router_hash": router_hash,
                        "timestamp": ts,
                        "event": "caps_change",
                        "old_ip": None,
                        "new_ip": None,
                        "old_asn": None,
                        "new_asn": None,
                        "old_caps": prev_caps,
                        "new_caps": row["caps"],
                        "old_transport": None,
                        "new_transport": None
                    })

                # -------------------------
                # TRANSPORT CHANGE
                # -------------------------
                if (
                    prev_transport is not None
                    and pd.notna(row["transport"])
                    and row["transport"] != prev_transport
                ):
                    events.append({
                        "router_hash": router_hash,
                        "timestamp": ts,
                        "event": "transport_change",
                        "old_ip": None,
                        "new_ip": None,
                        "old_asn": None,
                        "new_asn": None,
                        "old_caps": None,
                        "new_caps": None,
                        "old_transport": prev_transport,
                        "new_transport": row["transport"]
                    })

                # Update previous values
                prev_ip = row["ip"]
                prev_asn = row["asn"]
                prev_caps = row["caps"]
                prev_transport = row["transport"]

        return pd.DataFrame(events)

    # ---------------------------------------------------------
    # WRITE RESULTS
    # ---------------------------------------------------------
    def write_results(self, events_df):

        if events_df is None or len(events_df) == 0:
            print("[CHURN BUILDER] No churn events to write.")
            return

        # Replace table cleanly
        self.con.execute("DROP TABLE IF EXISTS router_churn")

        self.con.execute("""
            CREATE TABLE router_churn (
                router_hash TEXT,
                timestamp TIMESTAMP,
                event TEXT,
                old_ip TEXT,
                new_ip TEXT,
                old_asn INTEGER,
                new_asn INTEGER,
                old_caps TEXT,
                new_caps TEXT,
                old_transport TEXT,
                new_transport TEXT
            )
        """)

        self.con.register("events_df", events_df)
        self.con.execute("INSERT INTO router_churn SELECT * FROM events_df")

    # ---------------------------------------------------------
    # MAIN ENTRYPOINT
    # ---------------------------------------------------------
    def run(self):
        print("[CHURN BUILDER] Loading timeseries...")
        df = self.load_timeseries()

        if df.empty:
            print("[CHURN BUILDER] No timeseries data found.")
            return

        print("[CHURN BUILDER] Detecting churn events...")
        events_df = self.detect_churn(df)

        print(f"[CHURN BUILDER] Detected {len(events_df)} churn events.")

        print("[CHURN BUILDER] Writing churn table...")
        self.write_results(events_df)

        print("[CHURN BUILDER] Complete.")

    def close(self):
        try:
            self.con.close()
        except:
            pass


if __name__ == "__main__":
    builder = ChurnBuilder()
    builder.run()
    builder.close()

