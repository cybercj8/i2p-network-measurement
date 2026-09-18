import duckdb
import numpy as np
import pandas as pd
from sklearn.cluster import KMeans

DB_PATH = "data/i2p.duckdb"


class RouterRiskEngine:

    def __init__(self):
        self.con = duckdb.connect(DB_PATH)

    # ---------------------------------------------------------
    # LOAD ROUTER SNAPSHOT + ENRICHMENT DATA
    # ---------------------------------------------------------
    def load_router_data(self):
        return self.con.execute("""
            SELECT
                r.router_hash,
                r.asn,
                r.country_geo,
                r.router_age_days,
                r.supports_ipv6,
                r.supports_ntcp2,
                r.supports_ssu2,
                r.supports_ssu,
                r.bw_low,
                r.bw_mid,
                r.bw_high,
                r.bw_unlimited,
                r.latitude,
                r.longitude,
                r.version_major,
                r.version_minor,
                r.version_patch,
                r.version_build,

                a.risk_score AS asn_risk,
                a.reputation_tier,
                a.cluster_id,
                c.risk_score AS country_risk_score,

                t.threat_exposure AS threat_exposure,
                m.anomaly_score AS ml_anomaly

            FROM enriched_router_data r
            LEFT JOIN asn_info a USING (asn)
            LEFT JOIN country_risk_scores c
                ON r.country_geo = c.country
            LEFT JOIN threat_exposure t
                ON r.country_geo = t.country
            -- Aggregated to one row per router_hash before joining --
            -- router_anomalies can hold multiple historical rows per router,
            -- and joining against it directly fans this query out to one row
            -- per (router, anomaly event) pair instead of one row per router.
            LEFT JOIN (
                SELECT router_hash, AVG(anomaly_score) AS anomaly_score
                FROM router_anomalies
                GROUP BY router_hash
            ) m USING (router_hash)
            WHERE r.asn IS NOT NULL
        """).df()

    # ---------------------------------------------------------
    # LOAD UPTIME HISTORY
    # ---------------------------------------------------------
    def load_uptime_history(self):
        return self.con.execute("""
            SELECT router_hash, uptime_hours
            FROM timeseries_router_stats
            ORDER BY router_hash, timestamp DESC
        """).df()

    # ---------------------------------------------------------
    # LOAD CHURN EVENTS
    # ---------------------------------------------------------
    def load_churn_events(self):
        df = self.con.execute("""
            SELECT router_hash, timestamp, event
            FROM router_churn
            ORDER BY router_hash, timestamp DESC
        """).df()

        # FIX: force timestamp to BIGINT (ms)
        df["timestamp"] = df["timestamp"].astype("int64")

        return df

    # ---------------------------------------------------------
    # UPTIME ANOMALY (VOLATILITY + RESET DETECTION)
    # ---------------------------------------------------------
    def compute_uptime_anomaly(self, df):
        hist = self.load_uptime_history()
        anomalies = []

        for rh in df["router_hash"]:
            h = hist[hist["router_hash"] == rh]["uptime_hours"].values

            if len(h) < 3:
                anomalies.append(0.0)
                continue

            vol = np.std(h)
            resets = np.sum(np.diff(h) < -1)

            score = (vol / 72.0) + (resets / 10.0)
            anomalies.append(min(score, 1.0))

        return np.array(anomalies)

    # ---------------------------------------------------------
    # BANDWIDTH ANOMALY
    # ---------------------------------------------------------
    def compute_bandwidth_anomaly(self, df):
        bw = (
            df["bw_low"].astype(int) * 0.2 +
            df["bw_mid"].astype(int) * 0.4 +
            df["bw_high"].astype(int) * 0.7 +
            df["bw_unlimited"].astype(int) * 1.0
        )
        if bw.max() == 0:
            return np.zeros(len(df))
        return (bw / bw.max()).clip(0, 1)

    # ---------------------------------------------------------
    # VERSION RISK
    # ---------------------------------------------------------
    def compute_version_risk(self, df):
        # I2P's real-world version spread is narrow (patch-level differences
        # within the same 0.9.x branch, e.g. 62-69), so a fixed divisor tuned
        # for large major/minor jumps left every router clustered near-zero
        # (max observed was ~0.023 out of a possible 1.0) even though the
        # oldest routers in the sample were meaningfully behind the newest.
        # Min-max normalizing against the *actually observed* spread instead
        # of a fixed constant means the newest version present always scores
        # 0, the oldest always scores 1.0, and everything between scales
        # proportionally -- self-calibrating whether the real spread is a
        # handful of patch versions or, someday, a full major version jump.
        version_score = (
            df["version_major"].fillna(0) * 10000 +
            df["version_minor"].fillna(0) * 100 +
            df["version_patch"].fillna(0)
        )
        latest = version_score.max()
        oldest = version_score.min()
        if latest == oldest:
            return np.zeros(len(df))
        age = (latest - version_score).clip(0)
        return np.clip(age / (latest - oldest), 0, 1)

    # ---------------------------------------------------------
    # CLUSTER OUTLIER (ASN CLUSTER FREQUENCY)
    # ---------------------------------------------------------
    def compute_cluster_outlier(self, df):
        cluster_counts = df["cluster_id"].value_counts()
        outlier = df["cluster_id"].map(lambda c: 1 / cluster_counts.get(c, 1))
        return (outlier / outlier.max()).clip(0, 1)

    # ---------------------------------------------------------
    # TRANSPORT RISK (LEGACY PROTOCOLS)
    # ---------------------------------------------------------
    def compute_transport_risk(self, df):
        return (~df["supports_ntcp2"].astype(bool)).astype(float).clip(0, 1)

    # ---------------------------------------------------------
    # CAPS RISK (UNUSUAL CAPABILITIES)
    # ---------------------------------------------------------
    def compute_caps_risk(self, df):
        caps = (
            df["supports_ssu"].astype(int) +
            df["supports_ssu2"].astype(int)
        )
        return np.clip(caps / 3.0, 0, 1)

    # ---------------------------------------------------------
    # THREAT EXPOSURE RISK (COUNTRY-LEVEL)
    # ---------------------------------------------------------
    def compute_threat_exposure_risk(self, df):
        return df["threat_exposure"].fillna(0).clip(0, 1).values

    # ---------------------------------------------------------
    # ML ANOMALY RISK
    # ---------------------------------------------------------
    def compute_ml_anomaly_risk(self, df):
        return df["ml_anomaly"].fillna(0).clip(0, 1).values

    # ---------------------------------------------------------
    # CHURN RISK (SEVERITY + DECAY + FREQUENCY)
    # ---------------------------------------------------------
    def compute_churn_risk(self, df):
        churn = self.load_churn_events()

        if churn is None or churn.empty:
            return np.zeros(len(df))

        severity = {
            "ip_change": 1.0,
            "asn_change": 0.8,
            "transport_change": 0.6,
            "caps_change": 0.3
        }

        MS_PER_HOUR = 3600 * 1000
        MS_PER_DAY = 24 * MS_PER_HOUR
        MS_PER_WEEK = 7 * MS_PER_DAY
        TAU_HOURS = 12.0

        risks = []

        for rh in df["router_hash"]:
            events = churn[churn["router_hash"] == rh]

            if events.empty:
                risks.append(0.0)
                continue

            latest_ts = events["timestamp"].max()

            # Short-term (24h)
            window_24h = latest_ts - MS_PER_DAY
            recent = events[events["timestamp"] >= window_24h]

            short_term_score = 0.0

            for _, row in recent.iterrows():
                ev = row["event"]
                ts = row["timestamp"]

                age_hours = (latest_ts - ts) / MS_PER_HOUR
                decay = np.exp(-age_hours / TAU_HOURS)

                short_term_score += severity.get(ev, 0.3) * decay

            short_term_risk = min(short_term_score / 5.0, 1.0)

            # Long-term (7 days)
            window_7d = latest_ts - MS_PER_WEEK
            week_events = events[events["timestamp"] >= window_7d]
            weekly_count = len(week_events)

            freq_risk = min(weekly_count / 20.0, 1.0)

            churn_risk = 0.5 * short_term_risk + 0.5 * freq_risk
            risks.append(churn_risk)

        return np.array(risks)
 
    # ---------------------------------------------------------
    # ADVANCED BEHAVIORAL METRICS
    # ---------------------------------------------------------
    def compute_uptime_slope(self, df):
        slopes = []

        # Helper: normalize ANY timestamp → epoch ms (int)
        def _to_ms(t):
            # Already numeric (DuckDB BIGINT, numpy int, float)
            if isinstance(t, (int, float, np.integer, np.floating)):
                return int(t)

            # Pandas / Python datetime → epoch ms
            if hasattr(t, "timestamp"):
                return int(t.timestamp() * 1000)

            # Fallback: try casting
            return int(t)

        for rh in df["router_hash"]:
            ts = self.con.execute("""
                SELECT timestamp, uptime_hours
                FROM timeseries_router_stats
                WHERE router_hash = ?
                ORDER BY timestamp DESC
                LIMIT 20
            """, (rh,)).fetchall()

            if len(ts) < 3:
                slopes.append(0.0)
                continue

            # FIX: convert timestamps to epoch ms safely
            xs = np.array([_to_ms(t) for t, _ in ts], dtype=np.int64)
            ys = np.array([float(u) for _, u in ts], dtype=float)

            # Compute slope
            slope = np.polyfit(xs, ys, 1)[0]

            # Convert slope to risk score
            risk = np.clip(-slope / 1e7, 0, 1)
            slopes.append(risk)

        return np.array(slopes)
    
    def compute_stability_fingerprint(self, df):
        scores = []

        for rh in df["router_hash"]:
            rows = self.con.execute("""
                SELECT ip, asn, caps, transport
                FROM timeseries_router_stats
                WHERE router_hash = ?
                ORDER BY timestamp DESC
                LIMIT 50
            """, (rh,)).fetchall()

            if len(rows) < 3:
                scores.append(0.0)
                continue

            changes = sum(rows[i] != rows[i-1] for i in range(1, len(rows)))
            risk = min(changes / 20.0, 1.0)
            scores.append(risk)

        return np.array(scores)

    def compute_periodicity_score(self, df):
        scores = []

        # Helper: normalize ANY timestamp → epoch ms
        def _to_ms(t):
            if isinstance(t, (int, float, np.integer, np.floating)):
                return int(t)
            if hasattr(t, "timestamp"):
                return int(t.timestamp() * 1000)
            return int(t)

        for rh in df["router_hash"]:
            ts = self.con.execute("""
                SELECT timestamp
                FROM router_churn
                WHERE router_hash = ?
                ORDER BY timestamp DESC
                LIMIT 30
            """, (rh,)).fetchall()

            if len(ts) < 5:
                scores.append(0.0)
                continue

            # FIX: convert timestamps to epoch ms
            xs = np.array([_to_ms(t[0]) for t in ts], dtype=np.int64)

            # Compute deltas in ms
            deltas = np.diff(xs)

            # If all deltas identical → std = 0 → low risk
            std = float(np.std(deltas))

            # Normalize: high std → low periodicity, low std → high periodicity
            risk = np.clip((1e10 - std) / 1e10, 0, 1)
            scores.append(risk)

        return np.array(scores)

    def compute_behavioral_entropy(self, df):
        scores = []

        for rh in df["router_hash"]:
            rows = self.con.execute("""
                SELECT ip, asn, caps, transport
                FROM timeseries_router_stats
                WHERE router_hash = ?
                ORDER BY timestamp DESC
                LIMIT 50
            """, (rh,)).fetchall()

            if len(rows) < 5:
                scores.append(0.0)
                continue

            tokens = [str(r) for r in rows]
            values, counts = np.unique(tokens, return_counts=True)
            probs = counts / counts.sum()

            entropy = -np.sum(probs * np.log2(probs))
            risk = min(entropy / 5.0, 1.0)
            scores.append(risk)

        return np.array(scores)

    # ---------------------------------------------------------
    # BEHAVIORAL CLUSTERING (K=5)
    # ---------------------------------------------------------
    def compute_behavioral_clusters(self, df, scores, k=5):
        (
            risk,
            uptime,
            bandwidth,
            version,
            cluster,
            transport,
            caps,
            threat,
            ml_anom,
            churn,
            country,
            asn_risk,
            uptime_slope,
            stability,
            periodicity,
            entropy
        ) = scores

        X = np.vstack([
            uptime,
            churn,
            uptime_slope,
            stability,
            periodicity,
            entropy,
            version,
            ml_anom
        ]).T

        X = (X - X.mean(axis=0)) / (X.std(axis=0) + 1e-9)

        km = KMeans(n_clusters=k, n_init=10, random_state=42)
        labels = km.fit_predict(X)

        return labels

    # ---------------------------------------------------------
    # RISK EXPLANATION ENGINE
    # ---------------------------------------------------------
    def generate_risk_explanations(self, df, scores):
        (
            risk,
            uptime,
            bandwidth,
            version,
            cluster,
            transport,
            caps,
            threat,
            ml_anom,
            churn,
            country,
            asn_risk,
            uptime_slope,
            stability,
            periodicity,
            entropy
        ) = scores

        explanations = []

        for i in range(len(df)):
            reasons = []

            if churn[i] > 0.6:
                reasons.append("High churn instability (recent + frequent changes)")
            if uptime[i] > 0.6:
                reasons.append("Unstable uptime pattern (volatility or resets)")
            if uptime_slope[i] > 0.6:
                reasons.append("Downward uptime drift detected")
            if stability[i] > 0.6:
                reasons.append("High attribute volatility (IP/ASN/caps/transport)")
            if periodicity[i] > 0.6:
                reasons.append("Regular churn periodicity detected")
            if entropy[i] > 0.6:
                reasons.append("High behavioral entropy (unpredictable behavior)")
            if version[i] > 0.6:
                reasons.append("Outdated router version")
            if ml_anom[i] > 0.6:
                reasons.append("ML anomaly detected")
            if cluster[i] > 0.6:
                reasons.append("Cluster outlier (rare ASN behavior)")
            if threat[i] > 0.6:
                reasons.append("High threat exposure country")
            if country[i] > 0.6:
                reasons.append("High geopolitical risk region")
            if asn_risk[i] > 0.6:
                reasons.append("ASN associated with suspicious activity")
            if transport[i] > 0.6:
                reasons.append("Missing NTCP2 support")
            if caps[i] > 0.6:
                reasons.append("Unusual caps configuration")
            if bandwidth[i] > 0.6:
                reasons.append("Bandwidth configuration anomaly")

            if not reasons:
                reasons.append("No major red flags; moderate baseline risk")

            explanations.append("; ".join(reasons))

        return explanations

    # ---------------------------------------------------------
    # COMPOSITE RISK SCORE (ALL SIGNALS + BEHAVIORAL METRICS)
    # ---------------------------------------------------------
    def compute_composite_risk(self, df):
        uptime      = self.compute_uptime_anomaly(df)
        bandwidth   = self.compute_bandwidth_anomaly(df)
        version     = self.compute_version_risk(df)
        cluster     = self.compute_cluster_outlier(df)
        transport   = self.compute_transport_risk(df)
        caps        = self.compute_caps_risk(df)
        threat      = self.compute_threat_exposure_risk(df)
        ml_anom     = self.compute_ml_anomaly_risk(df)
        churn       = self.compute_churn_risk(df)

        uptime_slope = self.compute_uptime_slope(df)
        stability    = self.compute_stability_fingerprint(df)
        periodicity  = self.compute_periodicity_score(df)
        entropy      = self.compute_behavioral_entropy(df)

        country     = df["country_risk_score"].fillna(0.3).clip(0, 1).values
        asn_risk    = df["asn_risk"].fillna(0).clip(0, 1).values

        risk = (
            0.20 * uptime +
            0.10 * bandwidth +
            0.15 * version +
            0.10 * cluster +
            0.05 * transport +
            0.05 * caps +
            0.10 * threat +
            0.10 * ml_anom +
            0.10 * churn +
            0.05 * country +
            0.10 * asn_risk +
            0.05 * uptime_slope +
            0.05 * stability +
            0.05 * periodicity +
            0.05 * entropy
        )

        final_risk = np.clip(risk, 0, 1)

        return (
            final_risk,
            uptime, bandwidth, version, cluster,
            transport, caps, threat, ml_anom, churn,
            country, asn_risk,
            uptime_slope, stability, periodicity, entropy
        )


    # ---------------------------------------------------------
    # SUSPICIOUS ROUTER DETECTOR
    # ---------------------------------------------------------
    def detect_suspicious_routers(self, df, scores, clusters):
        """
        Computes a suspiciousness score for each router using:
          - risk score
          - ML anomaly
          - churn risk
          - uptime slope
          - stability fingerprint
          - periodicity score
          - behavioral entropy
          - cluster outlier score
          - ASN + country risk
          - distance from cluster centroid
        Returns a DataFrame sorted by suspiciousness.
        """

        (
            risk,
            uptime,
            bandwidth,
            version,
            cluster_outlier,
            transport,
            caps,
            threat,
            ml_anom,
            churn,
            country,
            asn_risk,
            uptime_slope,
            stability,
            periodicity,
            entropy
        ) = scores

        # -----------------------------------------------------
        # 1. Compute cluster centroid distances
        # -----------------------------------------------------
        X = np.vstack([
            uptime,
            churn,
            uptime_slope,
            stability,
            periodicity,
            entropy,
            version,
            ml_anom
        ]).T

        centroids = {}
        for c in np.unique(clusters):
            centroids[c] = X[clusters == c].mean(axis=0)

        cluster_dist = np.zeros(len(df))
        for i in range(len(df)):
            c = clusters[i]
            cluster_dist[i] = np.linalg.norm(X[i] - centroids[c])

        # Normalize cluster distance
        if cluster_dist.max() > 0:
            cluster_dist = cluster_dist / cluster_dist.max()

        # -----------------------------------------------------
        # 2. Suspiciousness score (weighted)
        # -----------------------------------------------------
        suspiciousness = (
            0.25 * risk +
            0.15 * ml_anom +
            0.15 * churn +
            0.10 * uptime_slope +
            0.10 * stability +
            0.10 * periodicity +
            0.10 * entropy +
            0.10 * cluster_outlier +
            0.10 * cluster_dist +
            0.05 * asn_risk +
            0.05 * country
        )

        suspiciousness = np.clip(suspiciousness, 0, 1)

        # -----------------------------------------------------
        # 3. Build output DataFrame
        # -----------------------------------------------------
        out = pd.DataFrame({
            "router_hash": df["router_hash"],
            "suspiciousness": suspiciousness,
            "risk_score": risk,
            "ml_anomaly": ml_anom,
            "churn_risk": churn,
            "uptime_slope": uptime_slope,
            "churn_stability": stability,
            "churn_periodicity": periodicity,
            "churn_entropy": entropy,
            "cluster_outlier": cluster_outlier,
            "cluster_distance": cluster_dist,
            "risk_cluster": clusters,
            "asn_risk": asn_risk,
            "country_risk": country
        })

        # Sort descending by suspiciousness
        out = out.sort_values("suspiciousness", ascending=False).reset_index(drop=True)

        return out

    # ---------------------------------------------------------
    # WRITE RESULTS (INCLUDES BEHAVIOR CLUSTER)
    # ---------------------------------------------------------
    def write_results(self, df):
        self.con.execute("DROP TABLE IF EXISTS router_risk_scores")

        # stability/periodicity/entropy/behavior_cluster are named
        # churn_* / risk_cluster here (not just stability/periodicity/
        # entropy/cluster) because router_behavior has its own,
        # completely unrelated fields under those exact same bare names --
        # computed from real per-router observation timestamps (see
        # ml/enriched_behavior_engine.py), not from IP/ASN/caps/transport
        # churn events like these are. Before this rename, the two tables
        # silently produced wildly different numbers under identical field
        # names with zero indication they meant different things.
        self.con.execute("""
            CREATE TABLE router_risk_scores (
                router_hash TEXT,
                risk_score DOUBLE,
                uptime_anomaly DOUBLE,
                bandwidth_anomaly DOUBLE,
                version_risk DOUBLE,
                cluster_outlier DOUBLE,
                transport_risk DOUBLE,
                caps_risk DOUBLE,
                threat_exposure DOUBLE,
                ml_anomaly DOUBLE,
                churn_risk DOUBLE,
                country_risk DOUBLE,
                asn_risk DOUBLE,
                uptime_slope DOUBLE,
                churn_stability DOUBLE,
                churn_periodicity DOUBLE,
                churn_entropy DOUBLE,
                risk_cluster INTEGER,
                risk_explanation TEXT
            )
        """)

        self.con.register("risk_df", df)
        self.con.execute("INSERT INTO router_risk_scores SELECT * FROM risk_df")

    # ---------------------------------------------------------
    # MAIN ENTRYPOINT
    # ---------------------------------------------------------
    def run(self):
        df = self.load_router_data()

        if df.empty:
            print("[RISK ENGINE] No router data found.")
            return

        scores = self.compute_composite_risk(df)
        (
            risk,
            uptime,
            bandwidth,
            version,
            cluster,
            transport,
            caps,
            threat,
            ml_anom,
            churn,
            country,
            asn_risk,
            uptime_slope,
            stability,
            periodicity,
            entropy
        ) = scores

        behavior_cluster = self.compute_behavioral_clusters(df, scores, k=5)
        explanations = self.generate_risk_explanations(df, scores)
        suspicious_df = self.detect_suspicious_routers(df, scores, behavior_cluster)
        print(suspicious_df.head(20))
        self.con.execute("DROP TABLE IF EXISTS suspicious_routers")
        self.con.execute("""
            CREATE TABLE suspicious_routers AS
            SELECT * FROM suspicious_df
        """)        
        suspicious_df.to_csv("suspicious_routers.csv", index=False)

        df_out = pd.DataFrame({
            "router_hash": df["router_hash"],
            "risk_score": risk,
            "uptime_anomaly": uptime,
            "bandwidth_anomaly": bandwidth,
            "version_risk": version,
            "cluster_outlier": cluster,
            "transport_risk": transport,
            "caps_risk": caps,
            "threat_exposure": threat,
            "ml_anomaly": ml_anom,
            "churn_risk": churn,
            "country_risk": country,
            "asn_risk": asn_risk,
            "uptime_slope": uptime_slope,
            "churn_stability": stability,
            "churn_periodicity": periodicity,
            "churn_entropy": entropy,
            "risk_cluster": behavior_cluster,
            "risk_explanation": explanations
        })

        self.write_results(df_out)
        print("[RISK ENGINE] Router risk scores computed successfully.")

    def close(self):
        try:
            self.con.close()
        except:
            pass


if __name__ == "__main__":
    engine = RouterRiskEngine()
    engine.run()
    engine.close()

