"""Regenerate the four stale (Jun 8) report figures from the current
enriched_router_data table so they match the numbers cited in
WRITEUP_DRAFT.md exactly. Style matched to the July 19 chart set
(orange bars, black edges) for visual consistency across all 8 figures.
"""
import duckdb
import matplotlib.pyplot as plt

con = duckdb.connect("data/i2p.duckdb", read_only=True)
OUTDIR = "visualizations"


def savefig(name):
    plt.tight_layout()
    plt.savefig(f"{OUTDIR}/{name}", dpi=150)
    plt.clf()


def bar(x, y, title, ylabel, xlabel="", figsize=(9, 5), rot=30):
    plt.figure(figsize=figsize)
    plt.bar(x, y, color="#ff8c00", edgecolor="black")
    plt.title(title)
    plt.ylabel(ylabel)
    if xlabel:
        plt.xlabel(xlabel)
    plt.xticks(rotation=rot, ha="right")


# Figure 1 — protocol version distribution (top 8 versions)
df = con.execute(
    "SELECT version_raw, count(*) c FROM enriched_router_data "
    "GROUP BY 1 ORDER BY c DESC LIMIT 8"
).fetchdf()
bar(df["version_raw"], df["c"], "Protocol Version Distribution (Top 8)",
    "Router Count", "I2NP Protocol Version")
savefig("version_distribution.png")

# Figure 2 — floodfill capability distribution
df = con.execute(
    "SELECT CASE WHEN is_floodfill_cap THEN 'Floodfill' ELSE 'Non-floodfill' END AS cap, "
    "count(*) c FROM enriched_router_data GROUP BY 1 ORDER BY c DESC"
).fetchdf()
bar(df["cap"], df["c"], "Floodfill Capability Distribution", "Router Count",
    figsize=(6, 5), rot=0)
savefig("caps_distribution.png")

# Figure 4 — top countries by router count
df = con.execute(
    "SELECT country_geo, count(*) c FROM enriched_router_data "
    "WHERE country_geo IS NOT NULL GROUP BY 1 ORDER BY c DESC LIMIT 10"
).fetchdf()
bar(df["country_geo"], df["c"], "Top 10 Countries by Router Count",
    "Router Count", "Country", rot=0)
savefig("top_countries.png")

# Figure 5 — top ASNs (as_org) by router count
df = con.execute(
    "SELECT as_org, count(*) c FROM enriched_router_data "
    "WHERE as_org IS NOT NULL GROUP BY 1 ORDER BY c DESC LIMIT 10"
).fetchdf()
bar(df["as_org"], df["c"], "Top 10 Autonomous Systems by Router Count",
    "Router Count", "Autonomous System")
savefig("top_asns.png")

print("Regenerated: version_distribution.png, caps_distribution.png, "
      "top_countries.png, top_asns.png")
