# I2P Research Dashboard — System Architecture

This document explains, end to end, how this project turns raw I2P network
traffic into the risk scores, behavioral clusters, and maps shown on the
dashboard. It follows the data from the moment the I2P router process starts
through every pipeline stage to the Flask app that renders it.

## 1. Starting Point: The I2P Router(s)

Everything begins with real I2P router processes running on the local
machine. I2P (the "Invisible Internet Project") is a peer-to-peer anonymity
network — every participant runs a **router**, and routers gossip with each
other to build a shared, partial view of the network called the **netDb**
(network database). This project does not simulate or scrape I2P from the
outside; it *is* an I2P participant, and the netDb it builds up locally is
the raw material for everything downstream.

As of this writing the project runs **two independent router
implementations side by side** rather than one — the reference Java
implementation and `i2pd`, a separate C++ implementation — each maintaining
its own netDb under its own directory:

| Vantage point | netDb path |
|---|---|
| `java_i2p` | `~/Library/Application Support/i2p/netDb/` |
| `i2pd` | `/opt/homebrew/var/lib/i2pd/netDb/` |

`pipeline/router_snapshot_writer.py`'s `NETDB_PATHS` dict is what encodes
this, and every parsed router is tagged with which of the two collected it
(`seen_by`). The reason to run two collectors rather than one is that a
single vantage point only sees the slice of the network its own router
happens to gossip with; comparing what `java_i2p` sees against what `i2pd`
sees is itself a data source (see the vantage-point analysis in Section 4a),
and it also means neither implementation's own routing quirks are mistaken
for a property of the network as a whole.

The Java router is launched via
`/opt/homebrew/Cellar/i2p/2.12.0/libexec/runplain.sh`, a vendor-provided
script that starts the actual Java process (`net.i2p.router.RouterLaunch`)
in the background via `nohup ... &` and then exits almost immediately
itself, leaving the router running detached. (I2P also ships a fancier
`i2prouter` service-wrapper script with built-in crash supervision, but its
underlying Java Service Wrapper binary does not work on Apple Silicon —
confirmed directly by testing it — which is exactly why `runplain.sh` is the
correct choice here.) `i2pd` runs as its own separate process/Homebrew
service; unlike the Java router it has no equivalent bespoke supervisor
script documented in this project, which is a gap worth closing (see
Section 7).

As each router runs, it continuously downloads and stores **routerInfo**
files — signed records describing other routers on the network (their
identity, IP address, supported transports, capabilities, and a publish
timestamp) — under its own netDb directory above. These two directories are
the single point of contact between the live I2P network and this project's
data pipeline: every pipeline run starts by reading whatever routerInfo
files currently exist in both of them.

Because launching the router via `runplain.sh` forks and exits quickly, a
small wrapper script, `scripts/i2p_router_supervisor.sh`, was added so the
process could be supervised properly under `launchd` (macOS's service
manager). The wrapper runs `runplain.sh`, reads the PID it writes to
`$TMPDIR/router.pid`, and then blocks — polling every 5 seconds — until that
PID actually exits. This means `launchd`'s own crash-detection (`KeepAlive`)
sees the *real* lifetime of the router process, not just the wrapper
script's instant return, so if the router ever crashes it gets restarted
automatically.

## 2. Pipeline Architecture

The entire data pipeline is orchestrated by `pipeline/run_all.py`, a single
script that imports every stage and runs them in a fixed order via a small
helper, `run_step(name, func)`, which times each stage, logs its start and
completion, and — critically — re-raises any exception after printing a
traceback. This is a deliberate "hard-fail" design: if any stage produces
bad data, the whole run aborts loudly rather than silently propagating
corrupted state into later stages.

The pipeline is organized into eleven logical phases (nine original, plus a
"Known Implementations" step folded into Phase 1 and a "Phase 8b" geographic
clustering pass inserted after Phase 8), each building on tables the
previous phases created. All state lives in a single DuckDB file,
`data/i2p.duckdb` — an embedded analytical database, essentially SQLite's
faster cousin for columnar/aggregate workloads, which is why so much of this
system is expressed as SQL queries rather than in-memory Python data
structures.

### Phase 1 — Raw Ingestion (`pipeline/router_snapshot_writer.py`)

This is the bridge from I2P's binary netDb format into the database. It
walks *both* vantage points' netDb directory trees (Section 1) looking for
files named `routerInfo-*.dat`, parses each one (first trying a real I2P
wire-format decoder, `ingest/java_routerinfo.py`, then falling back to naive
text scanning if that fails — routerInfo files are internally
compressed/signed binary structures, and not every one decodes cleanly,
which is where the harmless "decompressModern failed" warnings visible in
the logs come from), and extracts the router's identity hash, IP address,
transport protocols, capability flags, version string, and self-reported
`published` timestamp (epoch ms). Each parsed router becomes one row in
`router_snapshots`, keyed by **`(router_hash, timestamp, seen_by)`** — the
`seen_by` column (`java_i2p` or `i2pd`) was added when the second vantage
point was introduced, and every pipeline run adds a fresh timestamped
observation rather than overwriting, so this table is an ever-growing
historical log of "what did we see, when, and through which router."

`ingest/java_routerinfo.py` had two bugs worth noting because they silently
corrupted every downstream boolean and timestamp field until fixed: boxed
Java types (`java.lang.Boolean`, `java.lang.Long`) coming back from the wire
decoder weren't being unwrapped, so every boolean field stringified to
`"False"` — which Python treats as truthy — and `published` came through as
the literal string `'1783468046331'` rather than an integer.

#### Known Implementations (`pipeline/known_implementations.py`) — ground truth, not a classifier

Immediately after snapshot ingestion (and deliberately before Router
Enrichment, which joins against it), this step labels exactly the **two
routers this project itself controls** — the local `java_i2p` and `i2pd`
processes — by reading their own `router.info` files and matching on
`router_hash`. It writes `known_implementations(router_hash PK,
implementation, note)`.

This is explicitly *not* a general "detect what implementation any router
is running" classifier: I2P's `router.version` field is a shared I2NP
protocol version number, not an implementation identifier — this project's
own i2pd (2.60.0) and Java I2P (2.12.0) both report the identical
`"0.9.69"` — so there is no reliable signal in the netDb data to label any
*other* router's implementation. Every router besides the two self-known
ones intentionally has `implementation = NULL`.

### Phase 2 — Timeseries and Churn (`router_timeseries_builder.py`, `asn_intel/churn_builder.py`)

The timeseries builder takes the *latest* snapshot for each router and
computes `uptime_hours` — the elapsed time since that router was previously
observed — by comparing against `router_last_timeseries`, a table that
always holds exactly one row per router (its most recent state). This is
the foundation for several downstream metrics: a router seen for the first
time has no prior state to compare against, so its uptime slope is
necessarily zero until a second observation exists.

The churn builder compares consecutive observations of the same router to
detect meaningful changes — a new IP address, a different ASN, added or
dropped capabilities — and logs each as an event in `router_churn`. This
table feeds the rule-based half of anomaly detection later in the pipeline
(routers that hop between ASNs or churn IPs frequently get flagged
regardless of what a statistical model thinks).

### Phase 3 — ASN Mapping and Enrichment (`router_asn_map_builder.py`, `router_enrichment.py`)

`router_enrichment.py` is where raw router data becomes analyzable: it takes
the latest snapshot per router and runs it through two MaxMind GeoLite2
databases — one for ASN (Autonomous System Number) lookup, one for
city-level geolocation — producing latitude, longitude, country code, and
ASN/organization name for every router. The result is written to
`enriched_router_data`, which becomes the canonical, single source of truth
that nearly every later stage joins against, and now also LEFT JOINs
`known_implementations` and carries forward `seen_by`, `implementation`,
`published`, and a derived `days_since_published` from Phase 1. (`seen_by`
here reflects only the *latest* snapshot's vantage point — for a real
overlap analysis across both vantage points, query `router_snapshots`
directly, which is exactly what the vantage-point comparison in Section 4a
does.) This table also parses capability flags and bandwidth tier flags out
of the raw `caps` string, and splits the version string into
major/minor/patch/build components.

Two important, hard-won details from testing against the real I2P NetDB
spec rather than assumption:

- **IPv6 geolocation.** The geolocation lookup only works on an IP address
  that can actually be parsed, and an earlier version of this code only
  attempted that parse for IPv4 addresses — silently discarding geolocation
  for any router reachable only over IPv6 (roughly 18% of the network).
  Since MaxMind's databases resolve IPv6 addresses just as well as IPv4,
  this was corrected to attempt the lookup on whichever address family the
  router actually has. This same bug turned out to have been independently
  reimplemented in **three separate places** — `router_enrichment.py`'s
  City lookup, `router_asn_map_builder.py`'s ASN lookup
  (`extract_ip_for_asn`), and `asn_intel/enrichment.py`'s own standalone
  GeoIP lookups — all three needed the identical fix, and the last of those
  was eliminated entirely (see Phase 4 below) rather than fixed in place.
- **Capability-flag parsing.** `parse_caps()` was rewritten against the
  actual I2P NetDB capability-flag spec: `E` means "congested" (not
  "is_introducer" as an earlier version assumed) and `P` denotes a
  bandwidth tier (not "supports_peer_test"); two fabricated flags, `C` and
  `V` ("supports_client_only" / "supports_version_flag"), were removed
  entirely since they don't correspond to real capability letters and had
  always evaluated to zero. Bandwidth-tier bucketing was also corrected to
  cover the full K/L/M/N/O/P/X tier range — an earlier version only handled
  L/M/N/O, and mislabeled `O` as "unlimited" when it isn't the top tier.
  Every downstream column derived from caps
  (`is_congested_medium`/`is_congested_high`/`is_rejecting_tunnels`/`bw_low`/`bw_mid`/`bw_high`/`bw_unlimited`)
  inherits this fix.

`router_asn_map_builder.py` runs in parallel, building a historical log
(`router_asn_map`) of which ASN each router has belonged to over time, plus
a "current state" convenience table (`router_last_asn_map`) — the ASN
equivalent of what the timeseries builder does for uptime.

### Phase 4 — ASN-Level Analytics (`asn_intel/enrichment.py`, `analyzer.py`, `clusters.py`, `reputation.py`)

Everything so far has been router-level. This phase aggregates up to the
ASN level, building `asn_info` — one row per Autonomous System, with
router counts, capability totals, average router age, and (eventually)
risk and anomaly scores. `asn_intel/enrichment.py`'s `run_asn_enrichment()`
seeds this table from scratch each run by aggregating `enriched_router_data`
grouped by ASN — it now sources those aggregates directly from
`enriched_router_data` rather than running its own separate GeoIP lookups,
which eliminates the third independent copy of the IPv6-lookup bug noted in
Phase 3. `ASNAnalyzer` then *updates* those seeded rows with computed
statistics (floodfill ratio, IPv6 adoption, a simple age-based risk
heuristic) — it deliberately never inserts rows itself, which is why the
seeding step has to run first.

`ASNClusterEngine` groups ASNs into behavioral clusters using KMeans over
their structural features, and `ASNReputationEngine` computes an actual
**anomaly score** for each ASN: it runs PCA (principal component analysis)
to reduce each ASN's feature vector to two dimensions, then measures each
ASN's Euclidean distance from the centroid of all ASNs in that reduced
space. ASNs far from the "average" ASN — unusual combinations of size,
capability mix, version spread, bandwidth distribution — get a high anomaly
score. Reputation tiers (A/B/C/D) are then assigned by quartile of that
anomaly distribution, so exactly a quarter of ASNs fall into each tier by
construction.

This PCA/reputation machinery is described above as if it had always been
producing output, but for a stretch of this project's history it silently
wasn't: `ASNReputationEngine.extract_features()` referenced router-level
column names (`is_floodfill_cap`, `bw_low`, etc.) that don't exist on the
ASN-level `asn_info` table, which raised a `KeyError` on every run. Reputation
tiers and anomaly scores are only actually being computed now that this
column mismatch has been fixed — worth knowing if any historical export or
screenshot shows every ASN at a default/missing reputation tier, since that
reflects the engine having failed silently rather than a real absence of
signal.

### Phase 5 — Machine Learning Engines (`ml/router_anomaly_detection.py`, `ml/enriched_behavior_engine.py`)

Router-level anomaly detection combines two independent signals into the
`router_anomalies` table. The first is a genuine unsupervised model —
scikit-learn's `IsolationForest`, trained on each router's uptime, ASN,
IPv6 support, and capability-string length — which flags roughly 5% of
routers (the `contamination` parameter) as statistical outliers, storing
its continuous `decision_function` score (more negative means more
anomalous). The second is rule-based: routers with more than three
distinct ASNs or ten-plus churn events get flagged by name, with a `NULL`
score rather than a fabricated number, specifically so that averaging
scores across a router's history doesn't blend a meaningless placeholder in
with real statistical output.

The behavior engine runs `MiniBatchKMeans` clustering over a broader
structural feature vector (ASN, geography, version, every capability and
bandwidth flag) to assign each router to one of twelve behavioral clusters
(`cluster_id`, `cluster_distance`), written to `router_behavior`.

**This is also now where entropy, periodicity, and stability are actually
computed** — an inversion of an earlier design where this engine computed
metaphorical, cross-sectional versions of these metrics and a later stage
(Phase 8) overwrote them with a time-based definition. That later stage no
longer touches these three columns at all (see Phase 8); this engine's
`compute_temporal_behavior_metrics()` is now their sole owner, computing
genuinely temporal metrics from each router's observation history in
`timeseries_router_stats`:

- **`stability`** — a plain count of how many times the router has been
  observed. Requires only one observation to be meaningful.
- **`periodicity`** — `1 / (1 + CV)`, where CV is the coefficient of
  variation of the *intervals between* consecutive observations. Bounded to
  `(0, 1]` by construction: a value near 1.0 means the router is observed at
  almost perfectly regular intervals; lower values mean irregular,
  bursty observation gaps. Requires at least two observations (one
  interval); `NULL` below that.
- **`entropy`** — the Shannon entropy of the histogram of those same
  inter-observation intervals: `0.0` if every interval is identical (no
  disorder), higher for a more spread-out, unpredictable distribution of
  gaps. Requires at least three observations to bucket into a histogram;
  `NULL` below that.

All three are also appended to a new time-series table,
`router_behavior_history(router_hash, timestamp, cluster_id, entropy,
periodicity, stability)`, so how a router's behavioral fingerprint evolves
over successive pipeline runs is itself queryable, not just its
latest snapshot.

### Phase 6 — Risk Engines (`asn_intel/risk_engine.py`, `asn_intel/asn_risk_engine.py`)

This is where everything gathered so far gets synthesized into a single
router-level `risk_score`, written to `router_risk_scores`. `RouterRiskEngine`
has grown substantially past a simple weighted blend of uptime, bandwidth,
and ASN/country reputation — `compute_composite_risk()` now combines
**fifteen** weighted sub-scores:

```
risk = 0.20·uptime_anomaly  + 0.10·bandwidth      + 0.15·version_risk
     + 0.10·cluster_outlier + 0.05·transport       + 0.05·caps
     + 0.10·threat_exposure + 0.10·ml_anomaly       + 0.10·churn_risk
     + 0.05·country_risk    + 0.10·asn_risk         + 0.05·uptime_slope
     + 0.05·churn_stability + 0.05·churn_periodicity + 0.05·churn_entropy
```

Note the weights sum to **1.30, not 1.0**, and the final result is clipped
to `[0, 1]` — meaning a router that trips several signals at once gets
pinned to the ceiling by the clip, not because it's genuinely the most
extreme router possible. This is a known property of the current formula,
not a bug, but worth knowing when interpreting a `risk_score` of exactly
`1.0`.

Several of these sub-scores are new since the composite was first written:
`compute_version_risk()` now min-max normalizes against the *actually
observed* version spread in the current dataset rather than a fixed
divisor (the old fixed-divisor version left nearly every router near 0);
`compute_churn_risk()` applies exponential time-decay to churn severity
(`TAU_HOURS=12`) plus a 7-day frequency component; `compute_uptime_slope()`
fits a linear regression (`np.polyfit`) to a router's uptime history to
detect drift; and `compute_stability_fingerprint()` /
`compute_periodicity_score()` / `compute_behavioral_entropy()` compute a
**second, independent** stability/periodicity/entropy triple — this time
from IP/ASN/capability/transport churn patterns in `timeseries_router_stats`,
not from observation-interval timing like Phase 5's version. These two
triples measure genuinely different things and, for a period, shared the
exact same column names (`entropy`, `periodicity`, `stability`) despite
holding unrelated values — a real bug (see `/methodology`, and Section 8)
that was fixed by renaming this engine's versions to `churn_entropy`,
`churn_periodicity`, and `churn_stability` in `write_results()`.

`RouterRiskEngine` also runs a **third**, separate K=5 KMeans clustering
(`risk_cluster`, distinct from Phase 5/8's K=12 behavioral clustering) purely
to compute cluster-distance as a risk input, generates a per-router
plain-English `risk_explanation` string in `generate_risk_explanations()`
(built from whichever sub-scores exceed a 0.6 threshold — e.g. "Downward
uptime drift detected", "High behavioral entropy (unpredictable
behavior)"), and — as a side effect worth knowing about — writes
`suspicious_routers.csv` to the repository root on every run.

A parallel `suspicious_routers` table is computed from a related but
distinctly weighted formula emphasizing behavioral-outlier signals over raw
risk (`detect_suspicious_routers()`):

```
suspiciousness = 0.25·risk        + 0.15·ml_anomaly    + 0.15·churn
                + 0.10·uptime_slope + 0.10·churn_stability
                + 0.10·churn_periodicity + 0.10·churn_entropy
                + 0.10·cluster_outlier + 0.10·cluster_distance
                + 0.05·asn_risk    + 0.05·country
```

(weights sum to 1.25, same clip-to-ceiling caveat as above).

`UnifiedASNRiskEngine` does the analogous thing one level up, producing each
ASN's final `risk_score` from its reputation tier, its member routers'
average risk, its country's risk, and its own size — weighted 30/20/20/15/15
respectively; this formula is unchanged from its original design. Because
the ASN-level anomaly score (from Phase 4's PCA distance-from-centroid) is
an unbounded value while every other component here is a normalized 0–1
probability, it gets min-max normalized before being folded into this
weighted sum — without that step, a handful of extreme outlier ASNs would
swamp the formula and get pinned to the maximum possible risk score
regardless of anything else about them.

Both engines write their output using patterns chosen specifically to
survive being re-run every 30 minutes forever: `router_risk_scores` and
`suspicious_routers` are fully dropped and recreated each run (so stale
rows for routers no longer seen simply disappear), while the ASN-level
`UPDATE ... WHERE asn = ?` statements only ever touch rows that Phase 4
already seeded, never attempting to insert. This distinction — "full
rebuild" versus "update in place" — recurs throughout the pipeline, and
getting it wrong in either direction was the source of most of the bugs
uncovered while testing this system against a genuinely empty database
(see Section 8).

### Phase 7 — Geographic History (`router_geo_builder.py`)

A thin but important stage: it copies each router's current coordinates
into `router_locations` (always the latest position) and appends to
`router_location_history` (every position ever recorded, keyed by
`router_hash` + `timestamp`). This history is what a derived
movement-statistics table, built later in Phase 9, summarizes per router.

### Phase 8 — Behavior Rebuild (`rebuild_behavior.py`)

This late-stage script rebuilds `router_features`, `router_behavior`,
`router_clusters`, and `cluster_summary` from scratch (`DROP` then
`CREATE`, not incremental updates, so there's no risk of stale leftover
rows), re-running `MiniBatchKMeans` one more time on the finalized
risk/suspiciousness/anomaly/uptime feature set to produce each router's
final `cluster_id` and `cluster_distance`.

**It no longer computes entropy, periodicity, or stability at all** — that
ownership moved to Phase 5 (see above). What it does instead is preserve
Phase 5's values across its own `DROP`/rebuild of `router_behavior`: it
backs up the three columns into a temporary
`router_behavior_temporal_backup` table before dropping `router_behavior`,
then rejoins them onto the freshly computed `cluster_id`/`cluster_distance`.
This backup-and-rejoin step exists specifically because an earlier version
of this script *did* recompute these three columns itself (with a
non-temporal, purely uptime-value-based formula), silently discarding the
correct Phase 5 values on every single pipeline run — a real regression,
caught because periodicity was observed reaching values like 10.4 despite
being mathematically bounded to `(0, 1]` by Phase 5's definition. See
`/methodology` in the dashboard (Section 4c) and Section 8 for more on how
this was found.

### Phase 8b — Geographic Clustering (`ml/geo_clustering.py`, `ml/geo_clustering_sensitivity.py`)

A newer stage, deliberately separate from the mixed-feature behavioral
KMeans above: this clusters routers on **geography alone**, using DBSCAN
with a haversine (great-circle) distance metric over each router's
`(latitude, longitude)` from `enriched_router_data`. The radius is
configured as `CLUSTER_RADIUS_KM = 250` (`eps = 250/6371` radians) with
`MIN_SAMPLES = 5`; routers that don't fall within a dense-enough geographic
group get labeled noise (`geo_cluster_id = -1`). Output is a full
DELETE+INSERT rebuild of `router_geo_clusters(router_hash PK,
geo_cluster_id, latitude, longitude)` each run.

Because a DBSCAN radius is a somewhat arbitrary choice, a companion script,
`ml/geo_clustering_sensitivity.py`, re-runs the same clustering at three
radii — 100km, 250km, and 500km — and, beyond just counting clusters and
noise points at each radius, computes the **Adjusted Rand Index** between
each alternate radius's cluster assignments and the 250km baseline. This
answers a sharper question than cluster *counts* alone would: not just "do
we get a similar number of clusters at a different radius," but "are the
*same routers* actually grouped together." Results land in
`geo_clustering_sensitivity(radius_km, n_clusters, n_noise, noise_pct,
ari_vs_baseline, router_count)`.

### Phase 9 — Visualization Generation (`visualizations/maps/map_generator.py` and `visualizations/charts/*.py`)

The final phase renders everything computed above into static artifacts the
Flask app serves directly rather than computing on the fly, and has grown
considerably beyond a handful of chart types.

`map_generator.py` uses `folium` (a Python wrapper around the Leaflet.js
mapping library) to produce interactive HTML maps: global overviews colored
by risk, anomaly, or behavioral-cluster membership; per-router, per-ASN,
and per-cluster drill-down maps; a `geo_cluster_map.html` visualizing Phase
8b's DBSCAN groups; a `country_density_map.html` choropleth
(`folium.Choropleth` plus a GeoJSON tooltip overlay); several genuine
kernel-density heatmaps via `folium.plugins.HeatMap`
(`router_risk_heatmap.html`, `suspicious_heatmap.html`,
`country_heatmap.html`, `cluster_heatmap.html`, and per-router variants);
and movement-focused maps — `router_movement_map.html` (top-500 routers by
distance traveled) and an animated `top_movers_map.html` (curated top-20,
rendered with `TimestampedGeoJson`). These movement maps are backed by a
table this phase itself creates, `_write_movement_stats()` writing
`router_movement_stats(router_hash PK, total_km, distinct_locations,
observations, first_seen, last_seen)`, computed via haversine distance over
Phase 7's `router_location_history` — a genuinely new persisted table, not
just a derived chart.

The chart scripts use `matplotlib` to render histograms and scatter plots
(risk distributions, suspiciousness-vs-risk overlays, per-ASN and
per-cluster breakdowns) as PNG files, now including two newer chart
modules: `geo_correlation_charts.py` (seven functions covering
country-by-version-outdatedness, country-by-floodfill%, country-by-IPv6%, a
network-wide router-type breakdown, bandwidth-class distribution, and
country-by-bandwidth-tier / country-by-uptime) and `vantage_charts.py`
(the java_i2p/i2pd overlap trend — see Section 4 below). Both output
directories are pure derived data — nothing in them is authoritative, and a
full pipeline run regenerates all of it.

## 3. External Reference Data: Country Risk and Threat Intelligence

Two tables in the database are deliberately *not* touched by the main
pipeline: `country_risk_scores` and `threat_exposure`. These come from
external, real-world reference data rather than anything this project
observes about the I2P network itself, and they answer a different
question than everything above: not "is this router's *behavior* unusual,"
but "is this router's *country* independently known to be risky."

`threat_exposure` is built from live threat-intelligence feeds — Spamhaus
and abuse.ch blocklists — aggregated per country into botnet, malware, spam,
and general exposure rates, normalized to 0–1 and averaged into a single
`threat_exposure` figure. `country_risk_scores` combines that with the
World Bank's Global Cybersecurity Index (a real published national
cybersecurity-maturity ranking) via a simple weighted formula:

```
risk_score = 0.6 × (1 − normalized_GCI) + 0.4 × threat_exposure
```

A country with a poor cybersecurity index and high observed threat activity
scores close to 1; a well-defended, low-threat country scores close to 0.
This score is then joined into both the router-level and ASN-level risk
formulas in Phase 6 by matching each router's geolocated country against
this table. Because these tables represent independent ground truth rather
than accumulated I2P observations, they were deliberately *preserved* (and
rebuilt from their source CSVs) rather than wiped during the project's
"clean slate" reset — deleting them would have zeroed out a real,
meaningful risk signal for no reason.

## 4. Cross-Cutting Analysis: Vantage Points, Geography, and Statistics

Three newer capabilities don't fit neatly into a single pipeline phase
because they exist specifically to check the rest of the system against
itself — comparing data sources against each other, or testing whether
patterns elsewhere in the pipeline are statistically real rather than
noise.

### 4a. Vantage-Point Comparison (`visualizations/charts/vantage_charts.py`, `/vantage-comparison`)

Because Phase 1 now collects from two independent routers (`java_i2p` and
`i2pd`, Section 1), the set of routers each one has actually observed can be
compared directly. `vantage_overlap_trend_chart()` computes the daily
**Jaccard similarity** (`|both| / |either|`) between the two vantage
points' observed router sets from `router_snapshots`, tracked as a trend
over time rather than a single snapshot — deliberately, because both
routers can bootstrap from a shared reseed peer list on first start, which
would inflate apparent overlap early on without reflecting genuine ongoing
agreement. The `/vantage-comparison` dashboard page surfaces java-only,
i2pd-only, and both-seen router counts alongside this Jaccard percentage.

### 4b. Geographic Correlation and Significance Testing (`visualizations/charts/geo_correlation_charts.py`, `/geo-correlation`)

This is the first analysis in the project that directly asks whether the
*geographic* half of the data (country, geo-cluster) and the *behavioral*
half (risk, anomaly, floodfill status, bandwidth tier) actually relate to
each other, rather than presenting each independently. The `/geo-correlation`
route runs actual statistical tests, not just descriptive charts:

- **Wilson score confidence intervals** (`wilson_confidence_interval()`)
  around per-country floodfill% and IPv6% — chosen over a naive
  proportion because some countries have as few as 5 observed routers,
  where a plain confidence interval can behave badly.
- **Three chi-square tests of independence** (version-outdatedness × country,
  floodfill × country, bandwidth-tier × country), each with a **Bonferroni
  correction** for running three tests at once (`BONFERRONI_ALPHA = 0.05 /
  3`) and a **Cramér's V** effect-size figure, since statistical
  significance alone doesn't say whether an effect is large enough to
  matter.
- A **Kruskal-Wallis H-test** (`compute_geo_cluster_behavior_tests()`)
  asking whether Phase 8b's DBSCAN geo-clusters differ in risk score or
  anomaly score — i.e., whether geographic clustering incidentally captures
  a behavioral pattern too.
- A **Pearson correlation matrix** across all eight computed router metrics
  (`compute_metric_correlation_matrix()`, rendered as
  `global_charts.py::metric_correlation_heatmap()`) flagging any pair with
  `|r| ≥ 0.7` as redundant — useful for noticing if two differently-named
  scores are effectively measuring the same thing.

### 4c. Engineering Changelog (`/methodology`)

A static dashboard page documenting the bug fixes described throughout this
document (caps-flag mis-parsing, the IPv6 lookup gap, the entropy/periodicity
name collision between Phases 5 and 6, the `ASNReputationEngine` `KeyError`,
the Phase 8 regression that discarded temporal metrics, and others) in one
place, alongside the geo-clustering sensitivity table and the cross-metric
correlation matrix from 4b. Worth checking directly for the full, current
list rather than treating this document as the last word on which bugs have
been found — it is the more frequently updated of the two.

## 5. The Scoring Systems, Summarized

By the time data reaches the dashboard, a router carries several
conceptually distinct scores that are easy to conflate:

- **`risk_score`** — the composite output of `RouterRiskEngine`'s
  15-component weighted formula (Phase 6): how concerning this router looks
  overall, blending its own behavior with its ASN's and country's
  reputation. Weights sum to 1.30 and the result is clipped to `[0, 1]`, so
  a `risk_score` of exactly `1.0` means "hit the ceiling," not necessarily
  "the single worst router in the dataset."
- **`suspiciousness`** — a related but separately weighted score (weights
  sum to 1.25) emphasizing behavioral-outlier and churn signals over raw
  risk factors, including distance from a *third*, risk-specific K=5
  cluster (`risk_cluster`) distinct from the two behavioral clusterings
  below.
- **`risk_explanation`** — a plain-English string listing which specific
  sub-scores (of 15 possible) pushed a router's risk score up, generated
  alongside `risk_score` itself.
- **`anomaly_score`** — specifically the statistical/rule-based anomaly
  signal (IsolationForest plus churn/ASN-hopping rules); more negative
  means more anomalous, and it's the one metric here that's about
  *unusualness* rather than *danger*.
- **`entropy` / `periodicity` / `stability`** (on `router_behavior`,
  Phase 5) — genuinely temporal metrics derived from the *regularity of a
  router's observation timestamps* (interval entropy, interval
  coefficient-of-variation, and observation count respectively), owned by
  the enriched behavior engine and preserved (not recomputed) by the later
  behavior-rebuild stage.
- **`churn_entropy` / `churn_periodicity` / `churn_stability`** (on
  `router_risk_scores`, Phase 6) — a *different*, similarly-named but
  unrelated set of metrics derived from IP/ASN/capability/transport churn
  patterns rather than observation timing. These two triples were
  originally identically named despite measuring different things, which
  was a real bug (see `/methodology` and Section 8); the risk-engine
  versions were renamed with a `churn_` prefix specifically to make this
  distinction impossible to miss going forward.
- **`geo_cluster_id`** (Phase 8b) — pure-geography DBSCAN cluster
  membership, independent of every other clustering in this list (`-1` =
  noise, not part of any dense geographic group).
- **ASN and country risk scores** — the same conceptual "risk" idea, but
  aggregated to a coarser level and, in the country case, sourced from
  external data rather than I2P observation at all.

## 6. The Dashboard (`app/routes.py` and `app/templates/`)

The Flask app is intentionally simple: `app/routes.py` defines one route
per page, each running a handful of DuckDB queries against the tables
described above, packaging results into lightweight objects, and rendering
a matching Jinja2 template. Static chart PNGs and map HTML files generated
in Phase 9 are served directly from disk via dedicated
`/visualizations/charts/...` and `/visualizations/maps/...` routes. Every
page follows the same layout convention — filters, an intelligence-summary
box, analytics charts, maps, then the underlying data table — and a small
shared `style.css` plus `theme.js` add dark-mode support (following system
preference by default, with a manual toggle persisted in the browser).

Beyond the original one-route-per-entity-type pages, the app now includes
several pages specific to the newer analysis described in Section 4:
`/vantage-comparison`, `/geo-correlation`, `/movement` (top-50 routers
ranked by `total_km` from Phase 9's `router_movement_stats`), `/methodology`
(Section 4c), and a `/export.csv` route that includes the newer columns
(`seen_by`, `implementation`, `days_since_published`, `geo_cluster_id`)
alongside existing risk/behavior columns — and deliberately excludes raw IP
addresses, appropriate for a study of an anonymity network.

## 7. Keeping It Running: The Automation Layer

Three `launchd` agents (macOS's native service manager, chosen over `cron`
for its reliability on modern macOS and its ability to supervise and
restart crashed processes) keep the whole system running unattended:
one keeps the Java I2P router alive via the supervisor script from Section
1, one runs the full pipeline every 30 minutes through a wrapper script
(`scripts/run_pipeline_safe.sh`) with lock-file overlap protection (so a
slow run can never collide with the next scheduled one), and one keeps the
Flask dashboard itself running and auto-restarting if it ever crashes.
Together they mean the dashboard always reflects the network's state as of,
at most, thirty minutes ago — without anyone needing to manually trigger a
collection cycle.

`run_pipeline_safe.sh` also gained a **connectivity pre-check**: before
even acquiring its lock file, it curls Apple's captive-portal-check
endpoint (`http://captive.apple.com`) with a 5-second timeout and, if the
response doesn't contain `"Success"`, exits immediately and skips the whole
cycle. The reasoning, documented directly in the script: both vantage-point
routers need real internet connectivity to gossip with the network, and
running the pipeline against a router that's currently offline wouldn't
fail loudly — it would just quietly write a "no new routers observed"
data point that looks like a real (if uneventful) network signal rather
than what it actually is, a collection gap.

One gap in this section as written: the dual-vantage-point design (Section
1) means a *second* router process, `i2pd`, now needs to be running and
supervised for its half of `NETDB_PATHS` to ever populate, but no
`i2pd`-specific supervisor script or `launchd` agent exists alongside the
Java router's — `scripts/i2p_router_supervisor.sh` and its associated
`launchd` plist only cover the Java process. Worth closing this gap
directly rather than assuming `i2pd` is equally durable.

## 8. Engineering Notes: What a Truly Empty Database Revealed

This pipeline existed for months before it was ever actually tested against
a completely empty database — every prior run inherited tables and rows
that some earlier, manual setup step had already created. Deliberately
wiping the database and running the pipeline "as if for the first time"
surfaced several latent ordering bugs that are worth documenting, since
they explain some of the more defensive-looking code scattered throughout
the scripts described above:

- **Missing primary keys.** `router_snapshots` relied on `INSERT OR IGNORE`
  for deduplication, but its `CREATE TABLE` statement never actually
  declared a primary key — DuckDB had silently tolerated this only because
  the table already existed, with a key, from before this pipeline's
  current form existed. A truly fresh table exposed the missing constraint
  immediately.
- **Forward references to later stages.** Several early-stage scripts
  referenced tables that a *later* stage creates — `router_timeseries_builder.py`
  patched a `router_behavior` column and queried `router_churn` before
  either table existed yet, and a suspicious-detection step queried
  `router_risk_scores` a full stage before `RouterRiskEngine` creates it.
  These only ever worked by accident, riding on tables left over from
  previous complete runs.
- **An `UPDATE`-only bootstrap.** `ASNAnalyzer` was written to only ever
  update existing `asn_info` rows, with no path to create them — meaning
  nothing in the pipeline could ever populate that table from nothing. The
  actual seeding logic existed in an unused file, `asn_intel/enrichment.py`,
  which had simply never been wired into `run_all.py`.
- **History tables versus rebuild tables, confused.** `router_location_history`
  used a plain `INSERT` with a `(router_hash, timestamp)` primary key, which
  works perfectly the first time but throws a constraint violation the
  moment the pipeline runs a second time without every single router
  producing a brand-new timestamp — an near-guarantee on real network data,
  since not every router republishes itself every 30 minutes. This one in
  particular would have silently broken the very first scheduled automated
  run had it not been caught by deliberately running the pipeline twice
  in immediate succession before trusting it to `launchd`.

The common thread is that "does it run" and "does it run correctly from
zero, repeatedly, forever" are different questions, and only the second one
is actually true for an unattended, automated system. Every fix above
followed the same principle already used successfully elsewhere in the
codebase: tables that represent a point-in-time snapshot get fully dropped
and rebuilt each run, while tables that represent an accumulating history
get idempotent inserts (`OR IGNORE` / `OR REPLACE`) so re-running never
duplicates or crashes on data that's already there.

### Since Then: A Second Wave of Fixes (see `/methodology` for the live list)

The dual-vantage-point work, the risk-engine expansion, and the geographic
analysis described throughout this document surfaced a further round of
bugs, most already referenced in-line above but worth summarizing in one
place since together they're a useful reminder that "the pipeline runs
without crashing" and "the pipeline's numbers are correct" are, again, two
different claims:

- The **caps-flag mis-parsing** (`E`/`P` meaning, fabricated `C`/`V` flags,
  incomplete bandwidth-tier bucketing — Phase 3) and the **IPv6-lookup gap
  independently reimplemented in three files** (Phases 3 and 4) both
  silently produced *plausible-looking, wrong* numbers rather than errors,
  which is why they went unnoticed for as long as they did.
- The **entropy/periodicity/stability name collision** between Phase 5's
  observation-timing metrics and Phase 6's churn-based metrics (Section 5)
  — two unrelated computations sharing identical column names is the kind
  of bug that only surfaces when someone happens to compare the same
  router's values side by side and notices they disagree (in the case that
  surfaced it: entropy 1.296 vs. 0.393, periodicity 0.552 vs. 0.999,
  stability 34.0 vs. 0.6, cluster 7 vs. 0, all supposedly describing the
  same router at the same moment).
- `ASNReputationEngine`'s column-name mismatch (Phase 4) and `rebuild_behavior.py`'s
  temporal-metric overwrite (Phase 8) were both **silent-failure** and
  **silent-regression** bugs respectively — the former raised an exception
  that was presumably being swallowed or never triggered downstream
  checks, the latter didn't error at all, it just quietly produced
  mathematically-impossible values (periodicity above its `(0,1]` bound)
  that nothing was validating against.
- A dead pipeline step, `ml/router_suspicious_detection.py`, was removed
  from `run_all.py` entirely (Section 2, Phase 5 vs. 6 area) — it wrote to
  an orphaned table (`router_suspicious`) nothing downstream read, since
  the real suspiciousness table is `RouterRiskEngine`'s `suspicious_routers`,
  and it queried `router_risk_scores` before that table exists on a fresh
  database, so on a truly empty database it would crash with no benefit if
  it survived. Removing genuinely dead code turned out to be as valuable
  here as fixing live bugs.
- Additional fixes recorded on the live `/methodology` page but not
  elaborated on above include a **timeseries duplicate-row bug** (row count
  dropped from 452,156 to 178,049 once fixed), **leaked database
  connections**, and a **4-hour churn-timestamp shift bug** — check that
  page directly for the current, authoritative list rather than treating
  this document's account as exhaustive, since it is updated more often
  than this file is.
