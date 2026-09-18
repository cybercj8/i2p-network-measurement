import pandas as pd

# Load the raw ITU/World Bank GCI CSV
df = pd.read_csv("data/gci_raw.csv")

# Automatically detect which columns are year columns (e.g., "2018", "2020", "2024")
year_cols = [c for c in df.columns if c.isdigit()]
latest_year = max(year_cols)

print(f"Detected latest GCI year: {latest_year}")

# Keep only the country code + latest year
df_clean = df[["REF_AREA", latest_year]].copy()
df_clean.columns = ["country", "gci_score"]

# Normalize if needed (ITU uses 0–100 scale)
if df_clean["gci_score"].max() > 1:
    df_clean["gci_score"] = df_clean["gci_score"] / 100.0

# Save cleaned CSV
df_clean.to_csv("data/gci_worldbank.csv", index=False)

print("Saved cleaned file to data/gci_worldbank.csv")

