import duckdb
import pandas as pd
import numpy as np

DB_PATH = "data/i2p.duckdb"


class CountryRiskEngine:

    def __init__(self):
        self.con = duckdb.connect(DB_PATH)

    # ---------------------------------------------------------
    # LOAD INPUT DATA
    # ---------------------------------------------------------
    def load_gci(self):
        return pd.read_csv("data/gci_worldbank.csv")

    def load_threat_exposure(self):
        return pd.read_csv("data/threat_exposure.csv")

    # ---------------------------------------------------------
    # NORMALIZATION HELPERS
    # ---------------------------------------------------------
    def normalize(self, df, cols):
        for col in cols:
            if df[col].max() > 0:
                df[col] = df[col] / df[col].max()
        return df

    # ---------------------------------------------------------
    # COMPUTE COUNTRY RISK SCORE
    # ---------------------------------------------------------
    def compute_country_risk(self, gci, threat):
        # Normalize GCI (higher = safer)
        gci = self.normalize(gci, ["gci_score"])

        # Normalize threat exposure (higher = more dangerous)
        threat = self.normalize(threat, ["botnet", "malware", "spam", "exposed"])
        threat["threat_exposure"] = threat[["botnet", "malware", "spam", "exposed"]].mean(axis=1)

        # Merge datasets
        df = gci.merge(threat, on="country", how="outer")

        # Fill missing values
        df["gci_score"] = df["gci_score"].fillna(0.3)
        df["threat_exposure"] = df["threat_exposure"].fillna(0.3)

        # Compute final risk score
        df["risk_score"] = (
            0.6 * (1 - df["gci_score"]) +
            0.4 * df["threat_exposure"]
        ).clip(0, 1)

        return df[["country", "risk_score"]]

    # ---------------------------------------------------------
    # WRITE TO DUCKDB
    # ---------------------------------------------------------
    def write_results(self, df):
        self.con.execute("DROP TABLE IF EXISTS country_risk_scores")
        self.con.execute("""
            CREATE TABLE country_risk_scores (
                country TEXT,
                risk_score DOUBLE
            )
        """)
        self.con.register("risk_df", df)
        self.con.execute("INSERT INTO country_risk_scores SELECT * FROM risk_df")

    # ---------------------------------------------------------
    # MAIN ENTRYPOINT
    # ---------------------------------------------------------
    def run(self):
        gci = self.load_gci()
        threat = self.load_threat_exposure()

        df = self.compute_country_risk(gci, threat)
        self.write_results(df)

        print("[COUNTRY RISK ENGINE] country_risk_scores table updated.")

    def close(self):
        try:
            self.con.close()
        except:
            pass


# ---------------------------------------------------------
# PIPELINE‑FRIENDLY FUNCTION WRAPPER
# ---------------------------------------------------------
def run_country_risk():
    engine = CountryRiskEngine()
    engine.run()
    engine.close()


# ---------------------------------------------------------
# DIRECT EXECUTION
# ---------------------------------------------------------
if __name__ == "__main__":
    run_country_risk()

