import pandas as pd
import numpy as np
from .spamhaus_ingest import load_spamhaus
from .abusech_ingest import load_abusech
from .aggregator import aggregate

def normalize(df):
    for col in ["botnet", "malware", "spam", "exposed"]:
        if df[col].max() > 0:
            df[col] = df[col] / df[col].max()
    df["threat_exposure"] = df[["botnet","malware","spam","exposed"]].mean(axis=1)
    return df

def run_threat_exposure():
    feeds = {
        "spamhaus": load_spamhaus(),
        "abusech": load_abusech()
    }

    agg = aggregate(feeds)
    df = pd.DataFrame.from_dict(agg, orient="index").reset_index()
    df.columns = ["country","botnet","malware","spam","exposed"]

    df = normalize(df)
    df.to_csv("data/threat_exposure.csv", index=False)

    print("[THREAT ENGINE] Wrote data/threat_exposure.csv")

